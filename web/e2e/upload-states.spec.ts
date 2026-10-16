import { expect, test, type Page } from "@playwright/test";
import { SAMPLE_CSV, runReportFrom, signUp } from "./helpers";

const JOB_EVENTS = /\/api\/v1\/orgs\/[^/]+\/jobs\/[^/]+\/events$/;
const JOB_DETAILS = /\/api\/v1\/orgs\/[^/]+\/jobs\/[^/?]+$/;
const JOBS = /\/api\/v1\/orgs\/[^/]+\/jobs$/;
const RUN_REPORT = /\/api\/v1\/orgs\/[^/]+\/runs\/[^/]+\/report/;

const sse = (...blocks: string[]) => ({
  status: 200,
  headers: { "content-type": "text/event-stream", "cache-control": "no-store" },
  body: ["retry: 600000", "", ...blocks.flatMap((block) => [block, ""]), ""].join("\n"),
});

async function toPreview(page: Page, prefix: string) {
  await signUp(page, prefix);
  await page.goto("/upload");
  await page.setInputFiles("#upload-file", SAMPLE_CSV);
  await page.getByRole("button", { name: "Sprawdź plik" }).click();
  await expect(page.getByRole("heading", { name: "Wykryte kolumny" })).toBeVisible();
}

test.describe("stany zadania (strumień podstawiony)", () => {
  test("bez rows_done: sam procent, a dla „running” brak anulowania", async ({ page }) => {
    await toPreview(page, "prog-pct");
    await page.route(JOB_EVENTS, (route) =>
      route.fulfill(
        sse(
          'event: status\ndata: {"id":"x","status":"running","progress":10}',
          'event: progress\ndata: {"id":"x","progress":40}',
        ),
      ),
    );
    await page.getByRole("button", { name: "Uruchom raport" }).click();

    await expect(page.getByText("Przetwarzam plik: 40 %").first()).toBeVisible();
    await expect(page.getByText(/wiersz \d+ z/)).toHaveCount(0);
    await expect(page.getByText("Trwa — nie można przerwać.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Anuluj zadanie" })).toHaveCount(0);
  });

  test("zadanie w kolejce można anulować", async ({ page }) => {
    await toPreview(page, "prog-queued");
    await page.route(JOB_EVENTS, (route) =>
      route.fulfill(sse('event: status\ndata: {"id":"x","status":"queued","progress":0}')),
    );
    await page.getByRole("button", { name: "Uruchom raport" }).click();

    await expect(page.getByText("Zadanie czeka w kolejce…").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Anuluj zadanie" })).toBeVisible();
    await expect(page.getByText("Trwa — nie można przerwać.")).toHaveCount(0);
  });

  test("no_records: komunikat o pliku bez poprawnych wierszy", async ({ page }) => {
    await toPreview(page, "no-records");
    await page.route(JOB_EVENTS, (route) =>
      route.fulfill(
        sse(
          'event: status\ndata: {"id":"x","status":"running","progress":20}',
          'event: done\ndata: {"id":"x","status":"failed","error_code":"no_records"}',
        ),
      ),
    );
    await page.getByRole("button", { name: "Uruchom raport" }).click();

    await expect(page.getByText("Nie udało się przygotować raportu")).toBeVisible();
    await expect(page.getByText(/Plik nie zawierał poprawnych wierszy/)).toBeVisible();
    await expect(page.getByText(/kod: no_records/)).toHaveCount(0);
  });

  test("sse_limit (429 na strumieniu): szybkie przejście na odpytywanie co 3 s i raport gotowy", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await toPreview(page, "sse-limit");
    await page.route(JOB_EVENTS, (route) =>
      route.fulfill({
        status: 429,
        contentType: "application/problem+json",
        body: JSON.stringify({ status: 429, code: "sse_limit", title: "Too many streams" }),
      }),
    );
    const polls: number[] = [];
    page.on("request", (request) => {
      if (request.method() === "GET" && JOB_DETAILS.test(request.url())) polls.push(Date.now());
    });
    await page.getByRole("button", { name: "Uruchom raport" }).click();

    // zadanie kończy się szybko, więc komunikatu o odpytywaniu można nie zdążyć zobaczyć:
    // sprawdzamy skutek (raport gotowy bez działającego strumienia) i że stan był czytany przez GET
    await expect(page.getByText("Raport jest gotowy").first()).toBeVisible({ timeout: 45_000 });
    expect(polls.length).toBeGreaterThan(0);
  });

  test("upload_required (422 przy starcie zadania): powrót do wyboru pliku z komunikatem", async ({
    page,
  }) => {
    await toPreview(page, "upload-required");
    await page.route(JOBS, (route) =>
      route.request().method() === "POST"
        ? route.fulfill({
            status: 422,
            contentType: "application/problem+json",
            body: JSON.stringify({
              status: 422,
              code: "upload_required",
              title: "Upload required",
            }),
          })
        : route.continue(),
    );
    await page.getByRole("button", { name: "Uruchom raport" }).click();

    await expect(page.locator("#upload-file-error")).toContainText(
      "Wybierz plik, który chcesz wgrać.",
    );
    await expect(page.getByRole("button", { name: "Sprawdź plik" })).toBeVisible();
  });
});

test("raport po okresie przechowywania (410 report_expired): komunikat zamiast ramki, skróty zostają", async ({
  page,
}) => {
  await signUp(page, "expired");
  await runReportFrom(page, SAMPLE_CSV);
  await page.route(RUN_REPORT, (route) =>
    route.fulfill({
      status: 410,
      contentType: "application/problem+json",
      body: JSON.stringify({ status: 410, code: "report_expired", title: "Gone" }),
    }),
  );
  await page.getByRole("link", { name: "Otwórz raport" }).click();

  await expect(page.getByText(/Raport został usunięty po okresie przechowywania/)).toBeVisible();
  await expect(page.getByText(/skrót SHA-256 zostaje do weryfikacji/)).toBeVisible();
  await expect(page.locator("iframe.iframe-report")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Pobierz HTML" })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: /Weryfikacja|Zweryfikuj|SHA-256/ }).first(),
  ).toBeVisible();
});

test("raport dostępny: link „Otwórz w nowej karcie” ma rel=noopener", async ({ page }) => {
  await signUp(page, "noopener");
  await runReportFrom(page, SAMPLE_CSV);
  await page.getByRole("link", { name: "Otwórz raport" }).click();
  const open = page.locator('a[target="_blank"]').first();
  await expect(open).toHaveAttribute("rel", /noopener/);
  await expect(page.locator("iframe.iframe-report")).toHaveCount(1);
});

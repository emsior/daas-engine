import { expect, test } from "@playwright/test";
import { SAMPLE_CSV, signUp } from "./helpers";

const JOB_EVENTS = /\/api\/v1\/orgs\/[^/]+\/jobs\/[^/]+\/events$/;
const JOB_DETAILS = /\/api\/v1\/orgs\/[^/]+\/jobs\/[^/?]+$/;

test("postęp zadania przychodzi strumieniem SSE (bez odpytywania), raport gotowy", async ({
  page,
}) => {
  await signUp(page, "sse");
  await page.goto("/upload");
  await page.setInputFiles("#upload-file", SAMPLE_CSV);
  await page.getByRole("button", { name: "Sprawdź plik" }).click();
  await expect(page.getByRole("heading", { name: "Wykryte kolumny" })).toBeVisible();

  const polled: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "GET" && JOB_DETAILS.test(request.url())) polled.push(request.url());
  });
  const events = page.waitForResponse((response) => JOB_EVENTS.test(response.url()));
  await page.getByRole("button", { name: "Uruchom raport" }).click();

  const response = await events;
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("text/event-stream");
  await expect(page.getByText("Raport jest gotowy").first()).toBeVisible({ timeout: 60_000 });
  expect(polled, "zapasowe odpytywanie nie powinno być potrzebne").toHaveLength(0);
});

test("kreator pokazuje postęp z kolejnych zdarzeń SSE (procent oraz „wiersz X z Y”)", async ({
  page,
}) => {
  await signUp(page, "sse-ui");
  await page.goto("/upload");
  await page.setInputFiles("#upload-file", SAMPLE_CSV);
  await page.getByRole("button", { name: "Sprawdź plik" }).click();
  await expect(page.getByRole("heading", { name: "Wykryte kolumny" })).toBeVisible();

  // strumień podstawiony: stan „running”, potem postęp z licznikiem wierszy; połączenie zostaje otwarte
  await page.route(JOB_EVENTS, (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream", "cache-control": "no-store" },
      body: [
        "retry: 600000",
        "",
        'event: status\ndata: {"id":"x","status":"running","progress":5}',
        "",
        'event: progress\ndata: {"id":"x","progress":40,"rows_done":400,"rows_total":1000}',
        "",
        "",
      ].join("\n"),
    }),
  );
  await page.getByRole("button", { name: "Uruchom raport" }).click();

  const card = page.getByRole("heading", { name: "Przetwarzanie pliku" });
  await expect(card).toBeVisible();
  await expect(page.getByText(/Sprawdzam plik: wiersz 400 z 1[\s ]000/).first()).toBeVisible();
  await expect(page.locator("progress.progress")).toHaveAttribute("value", "40");
});

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { SAMPLE_CSV, UKSC_FIXTURE, runReportFrom, signUp } from "./helpers";

async function expectNoSeriousViolations(page: Page) {
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const theme of ["light", "dark"]) {
    await page.evaluate(
      (value) => document.documentElement.setAttribute("data-theme", value),
      theme,
    );
    const results = await new AxeBuilder({ page })
      .exclude("iframe.iframe-report") // treść raportu to osobny dokument w sandboxie
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    const serious = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(serious.map((v) => `${theme}: ${v.id}`)).toEqual([]);
  }
}

const EVIDENCE = /\/api\/v1\/orgs\/[^/]+\/evidence$/;
const RUN_DETAIL = /\/api\/v1\/orgs\/[^/]+\/runs\/[^/?]+$/;
const UPLOADS = /\/api\/v1\/orgs\/[^/]+\/uploads$/;

test.describe("status runu kolektora w widoku Artefakty", () => {
  for (const [status, badge, text] of [
    ["PARTIAL", "Niepełny (PARTIAL)", /paczka z kolektora jest niepełna/],
    ["FAILED", "Błąd (FAILED)", /zakończyło się błędem/],
  ] as const) {
    test(`${status}: oznaczenie i komunikat`, async ({ page }) => {
      await signUp(page, `evidence-${status.toLowerCase()}`);
      await runReportFrom(page, UKSC_FIXTURE);
      await page.route(EVIDENCE, async (route) => {
        const response = await route.fetch();
        const body = (await response.json()) as Record<string, unknown>;
        await route.fulfill({ response, json: { ...body, run_status: status } });
      });
      await page.goto("/evidence");
      const alert = page.getByRole("status").filter({ hasText: badge });
      await expect(alert).toBeVisible();
      await expect(alert).toContainText(text);
      await expect(page.getByRole("table")).toBeVisible();
      await expectNoSeriousViolations(page);
    });
  }

  test("SUCCESS: bez oznaczenia niepełnego runu", async ({ page }) => {
    await signUp(page, "evidence-success");
    await runReportFrom(page, UKSC_FIXTURE);
    await page.goto("/evidence");
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByText(/PARTIAL|FAILED/)).toHaveCount(0);
  });
});

test("metryki null (przychód <= 0): „n/d” zamiast NaN, 0,0 % albo pustki", async ({ page }) => {
  await signUp(page, "metrics-nd");
  await runReportFrom(page, SAMPLE_CSV);
  await page.route(RUN_DETAIL, async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { metrics?: Record<string, unknown> };
    await route.fulfill({
      response,
      json: {
        ...body,
        metrics: { ...body.metrics, revenue_total: -20, margin_pct: null, avg_order_value: null },
      },
    });
  });
  await page.getByRole("link", { name: "Otwórz raport" }).click();
  await expect(page.getByRole("heading", { name: "Podsumowanie" })).toBeVisible();
  const list = page.locator("dl.meta-list").filter({ hasText: "Marża" });
  await expect(
    list.locator("dt", { hasText: "Marża" }).locator("xpath=following-sibling::dd[1]"),
  ).toHaveText("n/d");
  await expect(
    list
      .locator("dt", { hasText: "Średnia wartość zamówienia" })
      .locator("xpath=following-sibling::dd[1]"),
  ).toHaveText("n/d");
  await expect(page.getByText(/wartość nie do wyliczenia/)).toBeVisible();
  await expect(page.locator("main")).not.toContainText(/NaN|Infinity|undefined/);
  await expect(page.getByRole("heading", { name: "Sprawdź sam" })).toBeVisible();
  await expect(page.locator("iframe.iframe-report")).toBeVisible();
  await expectNoSeriousViolations(page);
});

test("ostrzeżenia uploadu: pełna lista, bez ucięcia, w regionie statusu", async ({ page }) => {
  await signUp(page, "warnings");
  const warnings = [
    "3 komórek z formułami bez zapisanych wartości: zapisz plik ponownie w Excelu, aby zapisać wyniki formuł.",
    "7 wartości w kolumnie „Przychód netto z zamówień zrealizowanych w okresie” nie da się jednoznacznie odczytać (separator tysięcy lub dziesiętny).",
    "2 komórki dłuższe niż 64 znaki zostały skrócone.",
  ];
  await page.route(UPLOADS, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    return route.fulfill({
      status: 201,
      json: {
        id: "00000000-0000-4000-8000-000000000001",
        filename: "client_export_pl.csv",
        kind: "csv",
        bytes: 2800,
        rows: 12,
        created_at: "2026-10-10T08:00:00Z",
        retention_until: "2027-10-10T08:00:00Z",
        suggested_pipeline: "ecommerce_demo",
        ready: true,
        columns: ["data", "przychod"],
        mapping: { data: "date", przychod: "revenue" },
        unmapped_columns: [],
        generated: [],
        warnings,
      },
    });
  });
  await page.goto("/upload");
  await page.setInputFiles("#upload-file", SAMPLE_CSV);
  await page.getByRole("button", { name: "Sprawdź plik" }).click();
  const region = page.getByRole("status").filter({ hasText: "Uwagi do pliku" });
  await expect(region).toBeVisible();
  for (const warning of warnings)
    await expect(region.getByText(warning, { exact: true })).toBeVisible();
  await expect(region.locator("li")).toHaveCount(3);
  // żadnego poziomego przewijania ani ucięcia tekstu
  const overflow = await region.evaluate((el) => el.scrollWidth > el.clientWidth + 1);
  expect(overflow).toBe(false);
  await expectNoSeriousViolations(page);
});

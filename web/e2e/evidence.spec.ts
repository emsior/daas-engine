import { expect, test } from "@playwright/test";
import { UKSC_FIXTURE, runReportFrom, signUp } from "./helpers";

test("status artefaktów: stan pusty, potem tabela z paczki kolektora (fixture uksc_evidence)", async ({
  page,
}) => {
  await signUp(page, "evidence");

  // przed pierwszym uruchomieniem kolektora: stan pusty z przyciskiem do uploadu
  await page.goto("/evidence");
  await expect(page.getByRole("heading", { name: "Artefakty", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Nie ma jeszcze artefaktów" })).toBeVisible();

  // paczka JSON -> podgląd bez mappingu kolumn -> raport
  await runReportFrom(page, UKSC_FIXTURE);

  await page.goto("/evidence");
  const table = page.getByRole("table");
  await expect(table).toBeVisible();
  const rows = table.locator("tbody > tr");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThanOrEqual(5);
  await expect(table.getByText("ENC-01", { exact: true })).toBeVisible();
  await expect(table.getByText("art. 8 ust. 1 pkt 2 lit. k").first()).toBeVisible();
  await expect(page.getByText("WS-KSIEGOWOSC-01")).toBeVisible();

  // skrót artefaktu: 12 znaków w tabeli, pełny w tytule; status zawsze tekstem
  const hash = table.locator("code.hash").first();
  await expect(hash).toHaveAttribute("title", /^[0-9a-f]{64}$/);
  await expect(table.getByText(/^(Zgodne|Zaliczone|PASS)/).first()).toBeVisible();

  // rozwijany wiersz: komenda kolektora z przyciskiem Kopiuj, pełny skrót w grupach po 8
  const expand = table.locator("tbody button[aria-expanded]").first();
  await expand.click();
  await expect(expand).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("code.code").first()).toContainText("uksc-collector.ps1");
  await expect(page.locator(".row-details .hash-group")).toHaveCount(8);
});

test("pulpit po uruchomieniu kolektora pokazuje trzy liczby z etykietami", async ({ page }) => {
  await signUp(page, "evidence-kpi");
  await runReportFrom(page, UKSC_FIXTURE);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Raport tygodnia" })).toBeVisible();
  const kpis = page.locator(".kpi-row");
  await expect(kpis).toBeVisible();
  await expect(kpis.locator(".kpi")).toHaveCount(3);
});

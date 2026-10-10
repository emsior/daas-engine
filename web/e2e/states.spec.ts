import { expect, test } from "@playwright/test";
import { SAMPLE_CSV, runReportFrom, signUp } from "./helpers";

const RUNS = /\/api\/v1\/orgs\/[^/]+\/runs(\?.*)?$/;

/** Cztery stany widoku danych: loading (szkielet), error (z ponowieniem), empty i success. */
test("kokpit: loading -> error z ponowieniem -> empty -> success", async ({ page }) => {
  await signUp(page, "states");

  // loading: odpowiedź listy opóźniona, widać szkielet z aria-busy i komunikat dla czytników
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(RUNS, async (route) => {
    await gate;
    await route.continue();
  });
  await page.goto("/");
  await expect(page.locator('[aria-busy="true"]').first()).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Ładowanie raportów…" })).toBeAttached();
  release();

  // empty: pierwsze logowanie
  await expect(page.getByRole("heading", { name: "Nie masz jeszcze raportu" })).toBeVisible();
  await page.unroute(RUNS);

  // error: backend zwraca 500 (problem+json z request_id), widok pokazuje komunikat i „Spróbuj ponownie”
  await page.route(RUNS, (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/problem+json",
      body: JSON.stringify({
        type: "urn:daas:problem:internal-error",
        title: "Internal Server Error",
        status: 500,
        request_id: "req-e2e-500",
        code: "internal_error",
      }),
    }),
  );
  await page.reload();
  const alert = page.getByRole("alert").filter({ hasText: "Nie udało się pobrać raportów" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("req-e2e-500");

  // retry po przywróceniu backendu -> stan pusty, potem success po wgraniu raportu
  await page.unroute(RUNS);
  await alert.getByRole("button", { name: "Spróbuj ponownie" }).click();
  await expect(page.getByRole("heading", { name: "Nie masz jeszcze raportu" })).toBeVisible();

  await runReportFrom(page, SAMPLE_CSV);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Raport tygodnia" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Archiwum raportów" })).toBeVisible();
  await expect(page.locator(".kpi")).toHaveCount(3);
});

test("sesja wygasła: wywołanie API z 401 przenosi na /login", async ({ page, context }) => {
  await signUp(page, "expired");
  await context.clearCookies();
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
});

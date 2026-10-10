import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { SAMPLE_CSV, login, onboard, uniqueEmail } from "./helpers";

/**
 * Wymaga raportu w koncie testowym: spec sam przechodzi onboarding i wgrywa plik wzorcowy.
 * Kluczowa asercja: podczas liczenia skrótu przeglądarka nie wysyła ŻADNEGO żądania sieciowego,
 * więc zdanie „Plik nie został wysłany na serwer” jest prawdziwe.
 */
test("Sprawdź sam: skrót liczony lokalnie, zero żądań sieciowych", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]).catch(() => undefined);
  await login(page, uniqueEmail("verify"));
  await onboard(page);
  await page.goto("/upload");
  await page.setInputFiles("#upload-file", SAMPLE_CSV);
  await page.getByRole("button", { name: "Sprawdź plik" }).click();
  await page.getByRole("button", { name: "Uruchom raport" }).click();
  await page.getByRole("link", { name: "Otwórz raport" }).click({ timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "Sprawdź sam" })).toBeVisible();

  // pobierz raport HTML z sesją przeglądarki (ciasteczko Secure nie trafia do APIRequestContext po http)
  // i policz oczekiwany skrót
  const src = await page.locator("iframe.iframe-report").getAttribute("src");
  const bytes = await page.evaluate(async (url) => {
    const response = await fetch(url ?? "", { credentials: "include" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return Array.from(new Uint8Array(await response.arrayBuffer()));
  }, src);
  const body = Buffer.from(bytes);
  const expected = createHash("sha256").update(body).digest("hex");

  await expect(page.getByText("Plik nie został wysłany na serwer.")).toBeVisible();

  const requests: string[] = [];
  page.on("request", (req) => requests.push(`${req.method()} ${req.url()}`));

  await page.setInputFiles('input[type="file"]', {
    name: "raport.html",
    mimeType: "text/html",
    buffer: body,
  });
  const result = page.getByTestId("verify-result");
  await expect(result.getByText("Zgodne", { exact: true })).toBeVisible();
  expect(requests, `żądania podczas liczenia skrótu: ${requests.join(", ")}`).toHaveLength(0);

  // skrót w grupach po 8 i zgodny z liczonym po stronie testu
  await expect(result.locator(".hash-group")).toHaveCount(8);
  await expect(result.locator("code.hash")).toHaveText(expected);

  // zmieniony plik -> Niezgodne (ikona + tekst)
  await page.setInputFiles('input[type="file"]', {
    name: "raport.html",
    mimeType: "text/html",
    buffer: Buffer.concat([body, Buffer.from(" ")]),
  });
  await expect(result.getByText("Niezgodne", { exact: true })).toBeVisible();
  expect(requests).toHaveLength(0);

  // przycisk „Kopiuj całość” przy zapisanym skrócie HTML wkłada do schowka ciągły skrót (bez spacji)
  const copy = page.getByRole("button", { name: "Kopiuj całość: HTML" });
  await copy.click();
  await expect(page.getByRole("button", { name: "Skopiowano" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(expected);
});

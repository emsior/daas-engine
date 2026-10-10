import { expect, test } from "@playwright/test";
import { SAMPLE_CSV, login, onboard, uniqueEmail } from "./helpers";

test("logowanie, onboarding, wgranie pliku i otwarcie raportu", async ({ page }) => {
  // bez sesji kokpit przekierowuje na /login
  await page.goto("/upload");
  await expect(page).toHaveURL(/\/login$/);

  // walidacja: pusty e-mail -> podsumowanie błędów z fokusem
  await page.getByRole("button", { name: "Wyślij link logowania" }).click();
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeFocused();

  const email = uniqueEmail();
  await login(page, email);
  await onboard(page);

  // pierwszy ekran: stan pusty z plikiem wzorcowym
  await expect(page.getByRole("heading", { name: "Nie masz jeszcze raportu" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Pobierz plik wzorcowy" })).toHaveAttribute(
    "href",
    "/samples/client_export_pl.csv",
  );

  // upload: zły typ -> komunikat przy polu + podsumowanie z fokusem
  await page.getByRole("link", { name: "Wybierz plik" }).click();
  await expect(page.getByRole("heading", { name: "Wgraj plik", level: 1 })).toBeFocused();
  await page.setInputFiles("#upload-file", {
    name: "zdjecie.png",
    mimeType: "image/png",
    buffer: Buffer.from("x"),
  });
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeFocused();
  await expect(page.locator("#upload-file-error")).toContainText("Plik musi być w formacie CSV");

  // poprawny plik -> podgląd mappingu -> start -> postęp -> raport
  await page.setInputFiles("#upload-file", SAMPLE_CSV);
  await page.getByRole("button", { name: "Sprawdź plik" }).click();
  await expect(page.getByRole("heading", { name: "Wykryte kolumny" })).toBeFocused();
  await page.getByRole("button", { name: "Uruchom raport" }).click();
  await expect(page.getByText("Raport jest gotowy").first()).toBeVisible({ timeout: 60_000 });
  await page.getByRole("link", { name: "Otwórz raport" }).click();

  await expect(page).toHaveURL(/\/runs\/.+/);
  const frame = page.locator("iframe.iframe-report");
  await expect(frame).toHaveAttribute("sandbox", "");
  await expect(frame).toHaveAttribute("src", /\/api\/v1\/orgs\/.+\/runs\/.+\/report\?fmt=html$/);
  await expect(page.getByRole("link", { name: "Pobierz JSON" })).toBeVisible();

  // archiwum na kokpicie zawiera nowy raport
  await page.getByRole("link", { name: "Kokpit" }).first().click();
  await expect(page.getByRole("heading", { name: "Archiwum raportów" })).toBeVisible();
});

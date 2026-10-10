import { expect, test } from "@playwright/test";
import { SAMPLE_CSV, runReportFrom, signUp } from "./helpers";

test("ustawienia: zużycie, retencja i opt-in modelu językowego zapisują się w backendzie", async ({
  page,
}) => {
  await signUp(page, "settings");
  await runReportFrom(page, SAMPLE_CSV);

  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Ustawienia", level: 1 })).toBeVisible();

  // zużycie z GET /usage: jeden run i wgrane bajty w bieżącym miesiącu
  const usage = page.getByRole("heading", { name: "Zużycie w bieżącym miesiącu" }).locator("..");
  await expect(usage).toContainText(/Okres: \d{4}-\d{2}/);
  await expect(usage).toContainText("Uruchomienia raportów");

  // walidacja retencji: komunikat + fokus na podsumowaniu
  const retention = page.getByLabel("Okres przechowywania wgranych plików (dni)");
  await retention.fill("0");
  await page.getByRole("button", { name: "Zapisz ustawienia" }).click();
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeFocused();

  // poprawny zapis i trwałość po przeładowaniu (PATCH /orgs/{id})
  await retention.fill("30");
  await page.getByRole("switch", { name: /Pozwól na streszczenie/ }).check();
  await page.getByRole("button", { name: "Zapisz ustawienia" }).click();
  await expect(page.getByText("Ustawienia zapisane.").first()).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Okres przechowywania wgranych plików (dni)")).toHaveValue("30");
  await expect(page.getByRole("switch", { name: /Pozwól na streszczenie/ })).toBeChecked();
});

test("webhooki: dodanie (sekret pokazany raz), test, usunięcie", async ({ page }) => {
  await signUp(page, "webhooks");
  await page.goto("/settings");
  await expect(page.getByText("Nie dodano jeszcze webhooków.")).toBeVisible();

  // walidacja: adres musi być HTTPS
  await page.getByLabel("Adres URL (HTTPS)").fill("http://nie-https.example.test/h");
  await page.getByRole("button", { name: "Dodaj webhook" }).click();
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeFocused();

  const url = "https://hooks.example.test/daas";
  await page.getByLabel("Adres URL (HTTPS)").fill(url);
  await page.getByRole("button", { name: "Dodaj webhook" }).click();
  const dialog = page.getByRole("dialog", { name: "Zapisz sekret podpisu" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Zapisałem sekret" }).click();
  await expect(dialog).toBeHidden();

  const row = page.getByRole("row", { name: new RegExp(url.replace(/[.]/g, "\\.")) });
  await expect(row).toContainText("run.finished");
  await expect(row).toContainText("Aktywny");

  // test wysyłki do adresu, który nie istnieje: aplikacja pokazuje czytelny błąd, nie wyjątek
  await row.getByRole("button", { name: /Wyślij test/ }).click();
  await expect(page.getByText(/Test (wysłany|nie powiódł się)/).first()).toBeVisible();

  await row.getByRole("button", { name: /Usuń/ }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Usuń" }).click();
  await expect(page.getByText("Nie dodano jeszcze webhooków.")).toBeVisible();
});

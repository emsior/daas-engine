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
  const retention = page.getByLabel("Okres przechowywania danych (dni)");
  await retention.fill("0");
  await page.getByRole("button", { name: "Zapisz ustawienia" }).click();
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeFocused();

  // poprawny zapis i trwałość po przeładowaniu (PATCH /orgs/{id})
  await retention.fill("30");
  await page.getByRole("switch", { name: /Pozwól na streszczenie/ }).check();
  await page.getByRole("button", { name: "Zapisz ustawienia" }).click();
  await expect(page.getByText("Ustawienia zapisane.").first()).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Okres przechowywania danych (dni)")).toHaveValue("30");
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
  await expect(
    page
      .getByText(/Test (dostarczony \(2xx\)|nie powiódł się \((brak odpowiedzi|[345]xx)\))/)
      .first(),
  ).toBeVisible();

  await row.getByRole("button", { name: /Usuń/ }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Usuń" }).click();
  await expect(page.getByText("Nie dodano jeszcze webhooków.")).toBeVisible();
});

test("harmonogram: przypomnienie, walidacja strefy z API (422), zapis i trwałość po przeładowaniu", async ({
  page,
}) => {
  await signUp(page, "schedule");
  await page.goto("/settings");
  const card = page.getByRole("region", { name: "Harmonogram i powiadomienia" });
  await expect(card.getByText("Przypomnienie jest wyłączone.")).toBeVisible();

  await card.getByRole("switch", { name: /Przypominaj e-mailem/ }).check();
  await card.getByLabel("Dzień tygodnia").selectOption("1");
  await card.getByLabel("Godzina").selectOption("9");
  await card.getByRole("switch", { name: /Wyślij e-mail, gdy raport/ }).check();

  // strefa spoza bazy IANA: API odpowiada 422, komunikat przy polu i podsumowanie z fokusem
  const tz = card.getByLabel(/Strefa czasowa/);
  await tz.fill("Mars/Olympus");
  await card.getByRole("button", { name: "Zapisz harmonogram" }).click();
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeFocused();
  await expect(tz).toHaveAttribute("aria-invalid", "true");

  await tz.fill("Europe/Warsaw");
  await card.getByRole("button", { name: "Zapisz harmonogram" }).click();
  await expect(card.getByText("Harmonogram zapisany.")).toBeVisible();
  await expect(card.getByText(/Następne przypomnienie: .* 09:00 \(Europe\/Warsaw\)/)).toBeVisible();

  await page.reload();
  const reloaded = page.getByRole("region", { name: "Harmonogram i powiadomienia" });
  await expect(reloaded.getByRole("switch", { name: /Przypominaj e-mailem/ })).toBeChecked();
  await expect(reloaded.getByLabel("Dzień tygodnia")).toHaveValue("1");
  await expect(reloaded.getByLabel("Godzina")).toHaveValue("9");
  await expect(reloaded.getByRole("switch", { name: /Wyślij e-mail, gdy raport/ })).toBeChecked();
});

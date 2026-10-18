import { expect, test } from "@playwright/test";
import { SAMPLE_CSV, expectNoHorizontalOverflow, runReportFrom, signUp } from "./helpers";

// 375 px niezależnie od projektu: ekran główny i upload bez poziomego przewijania, cele dotyku >= 44 px
test.use({ viewport: { width: 375, height: 667 } });

test("375 px: ekran główny (stan pusty), upload i raport mieszczą się bez przewijania poziomego", async ({
  page,
}) => {
  await signUp(page, "mobile");

  // ekran główny: stan pusty, główne CTA ma co najmniej 44 px wysokości
  await expect(page.getByRole("heading", { name: "Nie masz jeszcze raportu" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  const cta = page.getByRole("link", { name: "Wybierz plik" });
  expect((await cta.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);

  // upload: wybór pliku
  await page.goto("/upload");
  await expect(page.getByRole("heading", { name: "Wgraj plik", level: 1 })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  const submit = page.getByRole("button", { name: "Sprawdź plik" });
  expect((await submit.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);

  // upload: podgląd mappingu (tabela jako karty) i postęp
  await page.setInputFiles("#upload-file", SAMPLE_CSV);
  await submit.click();
  await expect(page.getByRole("heading", { name: "Wykryte kolumny" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await runReportFrom(page, SAMPLE_CSV);
  await expectNoHorizontalOverflow(page);

  // ekran główny z danymi (karta raportu + archiwum) i raport
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Archiwum raportów" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page
    .getByRole("link", { name: /^Otwórz/ })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "Sprawdź sam" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

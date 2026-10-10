import { expect, test } from "@playwright/test";
import { login, onboard, uniqueEmail } from "./helpers";

test("klucze API: utworzenie (pokazany raz), kopiowanie i odwołanie z potwierdzeniem", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]).catch(() => undefined);
  await login(page, uniqueEmail("keys"));
  await onboard(page);

  await page.goto("/settings/api-keys");
  await expect(page.getByRole("heading", { name: "Klucze API", level: 1 })).toBeVisible();

  // walidacja: brak nazwy -> podsumowanie błędów z fokusem
  await page.getByRole("button", { name: "Utwórz klucz" }).click();
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeFocused();

  await page.getByLabel("Nazwa klucza").fill("n8n e2e");
  await page.getByRole("button", { name: "Utwórz klucz" }).click();

  // dialog z kluczem: modalny, klucz widoczny raz
  const dialog = page.getByRole("dialog", { name: "Zapisz klucz teraz" });
  await expect(dialog).toBeVisible();
  const key = (await dialog.getByTestId("revealed-key").textContent()) ?? "";
  expect(key.length).toBeGreaterThan(20);
  await dialog.getByRole("button", { name: "Kopiuj klucz" }).click();
  await expect(dialog.getByRole("button", { name: "Skopiowano" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();

  // na liście widać tylko prefiks, nigdy pełny klucz
  const row = page.getByRole("row", { name: /n8n e2e/ });
  await expect(row).toBeVisible();
  await expect(page.locator("body")).not.toContainText(key);

  // odwołanie wymaga potwierdzenia
  await row.getByRole("button", { name: /Odwołaj/ }).click();
  const confirm = page.getByRole("alertdialog");
  await expect(confirm).toBeVisible();
  await confirm.getByRole("button", { name: "Odwołaj klucz" }).click();
  await expect(row.getByText("Odwołany")).toBeVisible();
});

import { expect, test } from "@playwright/test";
import { login, signUp, uniqueEmail } from "./helpers";

test("zaproszenie członka: formularz, błędy, przyjęcie po pierwszym logowaniu zaproszonej osoby", async ({
  page,
  browser,
  baseURL,
}) => {
  const ownerEmail = await signUp(page, "owner", "Firma Zapraszająca");
  await page.goto("/settings/members");
  await expect(page.getByRole("heading", { name: "Członkowie", level: 1 })).toBeVisible();
  // lista: tylko właściciel
  const table = page.getByRole("table", { name: "Członkowie organizacji" });
  await expect(table.getByRole("row")).toHaveCount(2); // nagłówek + właściciel
  await expect(table).toContainText(ownerEmail);
  await expect(table).toContainText("Właściciel");

  // walidacja: zły adres -> podsumowanie błędów z fokusem
  await page.getByLabel("Adres e-mail zapraszanej osoby").fill("to-nie-jest-email");
  await page.getByRole("button", { name: "Wyślij zaproszenie" }).click();
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeFocused();

  // poprawne zaproszenie
  const invited = uniqueEmail("invited");
  await page.getByLabel("Adres e-mail zapraszanej osoby").fill(invited);
  await page.getByLabel("Rola").selectOption("member");
  await page.getByRole("button", { name: "Wyślij zaproszenie" }).click();
  await expect(page.getByText(`Zaproszenie wysłane na adres ${invited}.`)).toBeVisible();

  // zaproszona osoba loguje się po raz pierwszy: trafia od razu do organizacji (bez onboardingu)
  const guestContext = await browser.newContext({ baseURL, locale: "pl-PL" });
  const guest = await guestContext.newPage();
  await login(guest, invited);
  await expect(guest).toHaveURL(/\/$/);
  await expect(guest.getByText("Organizacja: Firma Zapraszająca")).toBeVisible();
  await guestContext.close();

  // właściciel widzi nowego członka z rolą
  await page.reload();
  await expect(table).toContainText(invited);
  await expect(table.getByRole("row", { name: new RegExp(invited) })).toContainText("Członek");
});

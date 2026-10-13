import { expect, test } from "@playwright/test";
import { login, newProjectContext, signUp, uniqueEmail } from "./helpers";

test("zarządzanie członkami: zaproszenie, cofnięcie, zmiana roli, usunięcie z potwierdzeniem", async ({
  page,
  browser,
}) => {
  const ownerEmail = await signUp(page, "mgr-owner", "Firma Zarządzana");
  await page.goto("/settings/members");
  await expect(page.getByRole("heading", { name: "Członkowie", level: 1 })).toBeVisible();

  // backend nie ma już „dodaj istniejącego użytkownika”: jest tylko formularz zaproszenia
  await expect(page.getByRole("button", { name: "Wyślij zaproszenie" })).toBeVisible();
  await expect(page.getByText("Dodaj użytkownika")).toHaveCount(0);

  // zaproszenie -> na liście oczekujących -> cofnięcie
  const revoked = uniqueEmail("revoked");
  await page.getByLabel("Adres e-mail zapraszanej osoby").fill(revoked);
  await page.getByRole("button", { name: "Wyślij zaproszenie" }).click();
  const pending = page.getByRole("table", { name: "Oczekujące zaproszenia do organizacji" });
  await expect(pending).toContainText(revoked);
  await pending.getByRole("button", { name: /Cofnij zaproszenie/ }).click();
  await expect(page.getByText("Brak oczekujących zaproszeń.")).toBeVisible();

  // zaproszona osoba loguje się i trafia do organizacji
  const invited = uniqueEmail("member");
  await page.getByLabel("Adres e-mail zapraszanej osoby").fill(invited);
  await page.getByRole("button", { name: "Wyślij zaproszenie" }).click();
  await expect(pending).toContainText(invited);
  const guestContext = await newProjectContext(browser);
  const guest = await guestContext.newPage();
  await login(guest, invited);
  await expect(guest).toHaveURL(/\/$/);

  // członek (rola „member”) nie widzi akcji zarządzania
  await guest.goto("/settings/members");
  await expect(guest.getByRole("heading", { name: "Członkowie", level: 1 })).toBeVisible();
  await expect(guest.getByRole("button", { name: "Wyślij zaproszenie" })).toHaveCount(0);

  // właściciel: własny wiersz bez akcji, cudzy z listą ról i przyciskiem usunięcia
  await page.reload();
  const members = page.getByRole("table", { name: "Członkowie organizacji" });
  const ownerRow = members.getByRole("row", { name: new RegExp(ownerEmail) });
  await expect(ownerRow.getByRole("combobox")).toHaveCount(0);
  await expect(ownerRow.getByRole("button")).toHaveCount(0);
  const guestRow = members.getByRole("row", { name: new RegExp(invited) });
  await guestRow.getByRole("combobox").selectOption("admin");
  await expect(guestRow.getByRole("combobox")).toHaveValue("admin");
  await page.reload();
  await expect(
    members.getByRole("row", { name: new RegExp(invited) }).getByRole("combobox"),
  ).toHaveValue("admin");

  // usunięcie wymaga potwierdzenia; fokus startuje na „Anuluj”
  await members
    .getByRole("row", { name: new RegExp(invited) })
    .getByRole("button", { name: /Usuń/ })
    .click();
  const confirm = page.getByRole("alertdialog");
  await expect(confirm).toContainText(invited);
  await expect(confirm.getByRole("button", { name: "Anuluj" })).toBeFocused();
  await confirm.getByRole("button", { name: "Usuń z organizacji" }).click();
  await expect(confirm).toBeHidden();
  await expect(members).not.toContainText(invited);

  // usunięty użytkownik traci dostęp (sesja w organizacji zakończona)
  await guest.goto("/");
  await expect(guest).not.toHaveURL(/\/$/);
  await guestContext.close();
});

test("blokada ostatniego właściciela: komunikat last_owner z odpowiedzi 409", async ({
  page,
  browser,
}) => {
  await signUp(page, "last-owner", "Firma Jeden Właściciel");
  const second = uniqueEmail("second-owner");
  await page.goto("/settings/members");
  await page.getByLabel("Adres e-mail zapraszanej osoby").fill(second);
  await page.getByLabel("Rola").selectOption("owner");
  await page.getByRole("button", { name: "Wyślij zaproszenie" }).click();
  const ctx = await newProjectContext(browser);
  const other = await ctx.newPage();
  await login(other, second);
  await ctx.close();
  await page.reload();

  // wyścig: serwer odpowiada last_owner, UI pokazuje komunikat zamiast cichej porażki
  await page.route(/\/members\/[^/]+$/, (route) =>
    route.request().method() === "PATCH"
      ? route.fulfill({
          status: 409,
          contentType: "application/problem+json",
          body: JSON.stringify({ title: "Conflict", status: 409, code: "last_owner" }),
        })
      : route.continue(),
  );
  const row = page
    .getByRole("table", { name: "Członkowie organizacji" })
    .getByRole("row", { name: new RegExp(second) });
  await row.getByRole("combobox").selectOption("member");
  await expect(
    page.getByRole("alert").filter({ hasText: "co najmniej jednego właściciela" }),
  ).toBeVisible();
});

import { expect, test } from "@playwright/test";
import { readLatestLoginToken, uniqueEmail } from "./helpers";

async function requestLink(page: import("@playwright/test").Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Adres e-mail").fill(email);
  await page.getByRole("button", { name: "Wyślij link logowania" }).click();
  await expect(page.getByRole("heading", { name: "Sprawdź skrzynkę" })).toBeVisible();
  return readLatestLoginToken(email);
}

test("link otwarty w tej samej przeglądarce loguje bez dodatkowego ekranu (adres z maila /login/verify)", async ({
  page,
}) => {
  const token = await requestLink(page, uniqueEmail("same"));
  await page.goto(`/login/verify?token=${token}`);
  await expect(page).toHaveURL(/\/onboarding$/);
});

test("link otwarty w innej przeglądarce wymaga potwierdzenia: ekran, zamaskowany adres, przycisk", async ({
  page,
  browser,
  baseURL,
}) => {
  const email = uniqueEmail("other");
  const token = await requestLink(page, email);

  const otherContext = await browser.newContext({ baseURL, locale: "pl-PL" });
  const other = await otherContext.newPage();
  await other.goto(`/login/verify?token=${token}`);
  const heading = other.getByRole("heading", {
    name: "Zalogować się w tej przeglądarce?",
    level: 1,
  });
  await expect(heading).toBeVisible();
  await expect(heading).toBeFocused();
  // adres jest zamaskowany: pełny e-mail nie pojawia się na ekranie
  await expect(other.locator("main")).not.toContainText(email);
  await expect(other.locator("main")).toContainText("@");
  // token nie został zużyty przez samo otwarcie: dalej nie ma sesji
  await expect(other.getByRole("button", { name: /^Zaloguj jako/ })).toBeVisible();

  await other.getByRole("button", { name: /^Zaloguj jako/ }).click();
  await expect(other).toHaveURL(/\/onboarding$/);

  // link jest jednorazowy: powtórne użycie w pierwszej przeglądarce kończy się błędem
  await page.goto(`/login?token=${token}`);
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeVisible();
  await otherContext.close();
});

test("„To nie ja”: rezygnacja wraca do formularza i zdejmuje token z adresu", async ({
  page,
  browser,
  baseURL,
}) => {
  const token = await requestLink(page, uniqueEmail("decline"));
  const otherContext = await browser.newContext({ baseURL, locale: "pl-PL" });
  const other = await otherContext.newPage();
  await other.goto(`/login/verify?token=${token}`);
  await other.getByRole("button", { name: "To nie ja, wróć" }).click();
  await expect(other).toHaveURL(/\/login$/);
  await expect(other.getByRole("heading", { name: "Zaloguj się", level: 1 })).toBeVisible();
  await otherContext.close();

  // prawowity właściciel nadal może użyć linku
  await page.goto(`/login?token=${token}`);
  await expect(page).toHaveURL(/\/onboarding$/);
});

test("ekran potwierdzenia po angielsku", async ({ page, browser, baseURL }) => {
  const token = await requestLink(page, uniqueEmail("en"));
  const otherContext = await browser.newContext({ baseURL, locale: "en-US" });
  const other = await otherContext.newPage();
  await other.goto("/login?lang=en");
  await other.goto(`/login/verify?token=${token}`);
  await expect(other.getByRole("heading", { name: "Sign in in this browser?" })).toBeVisible();
  await expect(other.getByRole("button", { name: /^Sign in as/ })).toBeVisible();
  await otherContext.close();
});

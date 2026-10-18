import { expect, test, type Page } from "@playwright/test";
import { signUp } from "./helpers";

const background = (page: Page) =>
  page.evaluate(() => getComputedStyle(document.body).backgroundColor);

test.describe("język i motyw (ekrany bez sesji)", () => {
  test("przełącznik PL/EN: treść, atrybut lang i trwałość po przeładowaniu", async ({ page }) => {
    await page.goto("/login");
    await expect(page.locator("html")).toHaveAttribute("lang", "pl");
    await expect(page.getByRole("heading", { name: "Zaloguj się", level: 1 })).toBeVisible();

    await page.getByRole("link", { name: /^EN/ }).click();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page.getByRole("heading", { name: "Sign in", level: 1 })).toBeVisible();
    await expect(page.getByRole("button", { name: "Send sign-in link" })).toBeVisible();

    await page.goto("/login");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");

    await page.getByRole("link", { name: /^PL/ }).click();
    await expect(page.getByRole("heading", { name: "Zaloguj się", level: 1 })).toBeVisible();
  });

  test("przełącznik motywu: jasny, ciemny, systemowy; wybór jest zapamiętany", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/login");
    const group = page.getByRole("group", { name: "Motyw" });

    await group.getByRole("button", { name: "Jasny" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    const light = await background(page);

    await group.getByRole("button", { name: "Ciemny" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(group.getByRole("button", { name: "Ciemny" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const dark = await background(page);
    expect(dark).not.toBe(light);

    // wybór przeżywa przeładowanie (ciasteczko czytane po stronie serwera, bez błysku)
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    expect(await background(page)).toBe(dark);

    // „Systemowy” zdejmuje atrybut i słucha prefers-color-scheme
    await group.getByRole("button", { name: "Systemowy" }).click();
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);
    expect(await background(page)).toBe(light);
    await page.emulateMedia({ colorScheme: "dark" });
    expect(await background(page)).toBe(dark);
  });
});

test("po zalogowaniu: język i motyw w nagłówku aplikacji", async ({ page }) => {
  await signUp(page, "prefs");
  const toggle = page.locator(".app-header__toggle");
  if (await toggle.isVisible()) await toggle.click(); // mobile: menu zwinięte
  await page.getByRole("group", { name: "Motyw" }).getByRole("button", { name: "Ciemny" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("link", { name: /^EN/ }).click();
  await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

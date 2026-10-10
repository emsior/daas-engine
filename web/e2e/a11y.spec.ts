import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { appendFileSync } from "node:fs";
import { SAMPLE_CSV, UKSC_FIXTURE, login, runReportFrom, signUp, uniqueEmail } from "./helpers";

/**
 * Dostępność (WCAG 2.2 AA): axe na ekranach w jasnym i ciemnym motywie (0 serious/critical),
 * nawigacja samą klawiaturą po głównym zadaniu (upload -> raport) oraz miejsce fokusu po podmianie widoku.
 */
// zmiana motywu animuje kolory (przejścia CSS); axe mierzy kontrast, więc wyłączamy ruch (prefers-reduced-motion)
test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
});

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

interface Finding {
  screen: string;
  theme: string;
  id: string;
  impact: string | null | undefined;
  nodes: number;
  target: string;
}

async function scan(page: Page, screen: string, theme: "light" | "dark"): Promise<Finding[]> {
  await page.evaluate((value) => document.documentElement.setAttribute("data-theme", value), theme);
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const findings = results.violations.map((violation) => ({
    screen,
    theme,
    id: violation.id,
    impact: violation.impact,
    nodes: violation.nodes.length,
    target: violation.nodes[0]?.target.join(" ") ?? "",
  }));
  const log = process.env.A11Y_LOG;
  if (log) {
    appendFileSync(
      log,
      `${screen}\t${theme}\t${findings.length}\t${findings.map((f) => `${f.id}(${f.impact})`).join(",")}\n`,
    );
  }
  return findings;
}

const blocking = (findings: Finding[]) =>
  findings.filter((f) => f.impact === "serious" || f.impact === "critical");

async function tabTo(page: Page, target: Locator, max = 60): Promise<void> {
  for (let i = 0; i < max; i += 1) {
    if (await target.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`Fokus nie dotarł do elementu w ${max} krokach Tab (pułapka lub brak dostępu)`);
}

test("axe: ekrany bez organizacji (login, onboarding) w obu motywach", async ({ page }) => {
  const all: Finding[] = [];
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Zaloguj się", level: 1 })).toBeVisible();
  for (const theme of ["light", "dark"] as const) all.push(...(await scan(page, "login", theme)));

  // stan „sprawdź skrzynkę” i błąd walidacji
  await page.getByRole("button", { name: "Wyślij link logowania" }).click();
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeVisible();
  for (const theme of ["light", "dark"] as const) {
    all.push(...(await scan(page, "login-błąd", theme)));
  }

  await login(page, uniqueEmail("a11y-onboarding"));
  await expect(page).toHaveURL(/\/onboarding$/);
  await expect(page.getByRole("heading", { name: /organizacj/i, level: 1 })).toBeVisible();
  for (const theme of ["light", "dark"] as const) {
    all.push(...(await scan(page, "onboarding", theme)));
  }
  expect(blocking(all), JSON.stringify(blocking(all), null, 2)).toEqual([]);
});

test("axe: kokpit pusty i z danymi, upload, raport, artefakty, klucze API, członkowie", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const all: Finding[] = [];
  await signUp(page, "a11y");
  const themes = ["light", "dark"] as const;

  await expect(page.getByRole("heading", { name: "Nie masz jeszcze raportu" })).toBeVisible();
  for (const theme of themes) all.push(...(await scan(page, "kokpit-pusty", theme)));

  await page.goto("/upload");
  await expect(page.getByRole("heading", { name: "Wgraj plik", level: 1 })).toBeVisible();
  for (const theme of themes) all.push(...(await scan(page, "upload", theme)));

  // upload: błąd, podgląd, postęp (zatrzymany strumień), gotowe
  await page.setInputFiles("#upload-file", {
    name: "a.png",
    mimeType: "image/png",
    buffer: Buffer.from("x"),
  });
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeVisible();
  for (const theme of themes) all.push(...(await scan(page, "upload-błąd", theme)));
  await page.setInputFiles("#upload-file", SAMPLE_CSV);
  await page.getByRole("button", { name: "Sprawdź plik" }).click();
  await expect(page.getByRole("heading", { name: "Wykryte kolumny" })).toBeVisible();
  for (const theme of themes) all.push(...(await scan(page, "upload-podgląd", theme)));
  await runReportFrom(page, SAMPLE_CSV);
  for (const theme of themes) all.push(...(await scan(page, "upload-gotowe", theme)));

  await runReportFrom(page, UKSC_FIXTURE);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Archiwum raportów" })).toBeVisible();
  for (const theme of themes) all.push(...(await scan(page, "kokpit-z-danymi", theme)));

  await page
    .getByRole("link", { name: /^Otwórz/ })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "Sprawdź sam" })).toBeVisible();
  await expect(page.locator("iframe.iframe-report")).toBeVisible();
  for (const theme of themes) all.push(...(await scan(page, "raport", theme)));

  await page.goto("/evidence");
  await expect(page.getByRole("table")).toBeVisible();
  for (const theme of themes) all.push(...(await scan(page, "artefakty", theme)));
  await page.locator("tbody button[aria-expanded]").first().click();
  for (const theme of themes) all.push(...(await scan(page, "artefakty-rozwinięte", theme)));

  await page.goto("/settings/api-keys");
  await expect(page.getByRole("heading", { name: "Klucze API", level: 1 })).toBeVisible();
  for (const theme of themes) all.push(...(await scan(page, "klucze-api", theme)));
  await page.getByLabel("Nazwa klucza").fill("a11y");
  await page.getByRole("button", { name: "Utwórz klucz" }).click();
  await expect(page.getByRole("dialog", { name: "Zapisz klucz teraz" })).toBeVisible();
  for (const theme of themes) all.push(...(await scan(page, "klucze-api-dialog", theme)));
  await page.keyboard.press("Escape");

  await page.goto("/settings/members");
  await expect(page.getByRole("heading", { name: "Członkowie", level: 1 })).toBeVisible();
  for (const theme of themes) all.push(...(await scan(page, "członkowie", theme)));

  await page.goto("/settings");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.waitForLoadState("networkidle");
  for (const theme of themes) all.push(...(await scan(page, "ustawienia", theme)));

  expect(blocking(all), JSON.stringify(blocking(all), null, 2)).toEqual([]);
});

test("klawiatura: zadanie główne (kokpit -> upload -> raport -> weryfikacja) bez pułapek fokusu", async ({
  page,
}) => {
  await signUp(page, "keyboard");
  await page.goto("/");

  // kokpit: z paska nawigacji do „Wybierz plik” samym Tab, Enter otwiera upload; fokus na h1
  const toUpload = page.getByRole("link", { name: "Wybierz plik" });
  await page.locator("body").press("Tab");
  await tabTo(page, toUpload);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Wgraj plik", level: 1 })).toBeFocused();

  // upload: plik (natywne okno wyboru pliku pomijamy), Tab do przycisku, Enter
  const input = page.locator("#upload-file");
  await tabTo(page, input);
  await page.setInputFiles("#upload-file", SAMPLE_CSV);
  await tabTo(page, page.getByRole("button", { name: "Sprawdź plik" }));
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Wykryte kolumny" })).toBeFocused();

  await tabTo(page, page.getByRole("button", { name: "Uruchom raport" }));
  await page.keyboard.press("Enter");
  // po zakończeniu fokus trafia na komunikat o gotowym raporcie
  const done = page.locator('.alert[role="status"]').filter({ hasText: "Raport jest gotowy" });
  await expect(done).toBeVisible({ timeout: 60_000 });
  await expect(done).toBeFocused();

  await tabTo(page, page.getByRole("link", { name: "Otwórz raport" }));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/runs\/.+/);
  await expect(page.getByRole("heading", { level: 1 })).toBeFocused();

  // raport: wszystkie elementy interaktywne osiągalne, Tab przechodzi do stopki i wraca Shift+Tab
  await tabTo(page, page.getByRole("link", { name: "Pobierz JSON" }));
  await tabTo(page, page.locator('input[type="file"]'));
  await tabTo(page, page.getByRole("button", { name: /^Kopiuj/ }).first());
  await tabTo(page, page.getByRole("link", { name: /umow/i }).last());
  await page.keyboard.press("Shift+Tab");
  const focusedTag = await page.evaluate(() => document.activeElement?.tagName ?? "");
  expect(focusedTag).not.toBe("BODY");
});

test("fokus po podmianie widoku: błąd uploadu, nawigacja klienta, nieudane zadanie", async ({
  page,
}) => {
  await signUp(page, "focus");

  // nawigacja klienta (nav) przenosi fokus na h1 nowego widoku
  for (const [link, heading] of [
    ["Artefakty", "Artefakty"],
    ["Ustawienia", "Ustawienia"],
  ] as const) {
    const menu = page.locator(".app-header__toggle");
    if (await menu.isVisible()) await menu.click();
    await page
      .getByRole("navigation", { name: "Nawigacja główna" })
      .getByRole("link", { name: link })
      .click();
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeFocused();
  }

  // błąd uploadu -> podsumowanie błędów ma fokus, a link w nim prowadzi do pola
  await page.goto("/upload");
  await page.setInputFiles("#upload-file", {
    name: "a.png",
    mimeType: "image/png",
    buffer: Buffer.from("x"),
  });
  const summary = page.getByRole("group", { name: "Wystąpił problem" });
  await expect(summary).toBeFocused();
  await summary.getByRole("link").click();
  await expect(page.locator("#upload-file")).toBeFocused();

  // nieudane zadanie (strumień podstawiony) -> komunikat o błędzie ma fokus
  await page.setInputFiles("#upload-file", SAMPLE_CSV);
  await page.getByRole("button", { name: "Sprawdź plik" }).click();
  await expect(page.getByRole("heading", { name: "Wykryte kolumny" })).toBeFocused();
  await page.route(/\/jobs\/[^/]+\/events$/, (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: 'event: done\ndata: {"id":"x","status":"failed","error_code":"internal_error"}\n\n',
    }),
  );
  await page.getByRole("button", { name: "Uruchom raport" }).click();
  const failed = page.getByText("Nie udało się przygotować raportu");
  await expect(failed).toBeVisible();
  await expect(page.locator(":focus")).toContainText("Nie udało się przygotować raportu");
});

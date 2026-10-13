import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type BrowserContextOptions,
  type Page,
} from "@playwright/test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

/** Unikalny adres e-mail na każdy przebieg (backend tworzy użytkownika przy pierwszym logowaniu). */
export function uniqueEmail(prefix = "e2e"): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.test`;
}

/**
 * Odczytuje najnowszy link/token logowania z outboxa adaptera `console`
 * (katalog `E2E_OUTBOX_DIR`, domyślnie ../runtime/outbox względem web/).
 */
export function readLatestLoginToken(email: string): string {
  const dir = process.env.E2E_OUTBOX_DIR ?? path.resolve(__dirname, "../../runtime/outbox");
  const files = readdirSync(dir)
    .map((name) => ({ name, mtime: statSync(path.join(dir, name)).mtimeMs }))
    .sort((a, b) => b.mtime - a.mtime);
  for (const file of files) {
    const content = readFileSync(path.join(dir, file.name), "utf8");
    if (!content.includes(email)) continue;
    // wiadomości z linkiem logowania mają `token=`; zaproszenia wskazują tylko /login (pomijamy je)
    const match = /token=([A-Za-z0-9_\-.~]+)/.exec(content);
    if (match?.[1]) return match[1];
  }
  throw new Error(`Brak wiadomości dla ${email} w ${dir}`);
}

/**
 * Czeka, aż React zahydruje element (dostaje własności `__reactProps$…`). Tekst wpisany w pole przed hydracją
 * jest przy niej kasowany (pole kontrolowane), co w WebKicie, wolniejszym przy starcie, dawało losowy
 * błąd „Podaj adres e-mail.” przy wysyłce formularza.
 */
export async function waitForHydration(page: Page, selector: string): Promise<void> {
  const timeout = 15_000;
  try {
    await page.waitForFunction(
      (css) => {
        const element = document.querySelector(css);
        return (
          element !== null && Object.keys(element).some((key) => key.startsWith("__reactProps$"))
        );
      },
      selector,
      { timeout },
    );
  } catch (error) {
    throw new Error(
      `${selector} nie zahydrowany w ${timeout / 1000} s - znacznik Reacta __reactProps$ nie znaleziony ` +
        "(zmiana wnętrza Reacta albo kokpit się nie uruchomił?)",
      { cause: error },
    );
  }
}

/** Formularz logowania: wpisuje adres i prosi o link; kończy na ekranie „Sprawdź skrzynkę”. */
export async function requestLoginLink(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await waitForHydration(page, "#login-email");
  await page.getByLabel("Adres e-mail").fill(email);
  await page.getByRole("button", { name: "Wyślij link logowania" }).click();
  await expect(page.getByRole("heading", { name: "Sprawdź skrzynkę" })).toBeVisible();
}

/** Logowanie magic linkiem przez pole „Mam link lub token”. */
export async function login(page: Page, email: string): Promise<void> {
  await requestLoginLink(page, email);
  const token = readLatestLoginToken(email);
  await page.getByText("Mam link lub token").click();
  await page.getByLabel("Link lub token").fill(token);
  await page.getByRole("button", { name: "Zaloguj" }).click();
}

export async function onboard(page: Page, orgName = "Firma Testowa"): Promise<void> {
  await expect(page).toHaveURL(/\/onboarding$/);
  await page.getByLabel("Nazwa organizacji").fill(orgName);
  await page.getByRole("checkbox", { name: /Akceptuję umowę powierzenia/ }).check();
  await page.getByRole("button", { name: "Utwórz organizację" }).click();
  await expect(page).toHaveURL(/\/$/);
}

export const SAMPLE_CSV = path.resolve(__dirname, "../public/samples/client_export_pl.csv");

/** Paczka kolektora (fixture): wszystkie kontrole PASS. */
export const UKSC_FIXTURE = path.resolve(__dirname, "../../data/fixtures/uksc/host_compliant.json");

/** Wgrywa plik w kreatorze, uruchamia raport i czeka na ekran „Raport jest gotowy”. */
export async function runReportFrom(
  page: Page,
  file: string | { name: string; mimeType: string; buffer: Buffer },
) {
  await page.goto("/upload");
  await page.setInputFiles("#upload-file", file);
  await page.getByRole("button", { name: "Sprawdź plik" }).click();
  await page.getByRole("button", { name: "Uruchom raport" }).click();
  await expect(page.getByText("Raport jest gotowy").first()).toBeVisible({ timeout: 60_000 });
}

/** Logowanie + onboarding w jednym kroku (świeże konto z pustą organizacją). */
export async function signUp(
  page: Page,
  prefix = "e2e",
  orgName = "Firma Testowa",
): Promise<string> {
  const email = uniqueEmail(prefix);
  await login(page, email);
  await onboard(page, orgName);
  return email;
}

/** Strona nie może mieć poziomego przewijania (WCAG 1.4.10 Reflow). */
export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, "poziome przewijanie strony").toBeLessThanOrEqual(0);
}

/**
 * Drugi (np. „obcy”) kontekst przeglądarki z opcjami projektu. `browser.newContext()` nie dziedziczy `use`
 * z playwright.config.ts, więc bez tego helpera gość nie dostałby `ignoreHTTPSErrors` (stos e2e z E2E_TLS=1)
 * ani strefy czasowej. Wywołuj w treści testu; `overrides` nadpisują wartości projektu (np. `{ locale: "en-US" }`).
 */
export async function newProjectContext(
  browser: Browser,
  overrides: BrowserContextOptions = {},
): Promise<BrowserContext> {
  const { baseURL, ignoreHTTPSErrors, locale, timezoneId } = test.info().project.use;
  return browser.newContext({ baseURL, ignoreHTTPSErrors, locale, timezoneId, ...overrides });
}

const CLIPBOARD_KEY = "__e2eClipboard";

function isChromium(context: BrowserContext): boolean {
  return context.browser()?.browserType().name() === "chromium";
}

/**
 * Przygotowuje schowek przed nawigacją. Chromium: prawdziwe uprawnienia, odczyt przez `readText`.
 * Firefox i WebKit nie pozwalają Playwrightowi odczytać schowka, więc `navigator.clipboard.writeText` jest
 * przechwytywane (ostatni zapisany tekst ląduje w `window.__e2eClipboard`); odczyt robi `readClipboard`.
 */
export async function prepareClipboard(context: BrowserContext): Promise<void> {
  if (isChromium(context)) {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]).catch(() => undefined);
    return;
  }
  await context.addInitScript((key) => {
    const target = window as unknown as Record<string, unknown>;
    target[key] = null;
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText = (text: string) => {
      target[key] = String(text);
      return Promise.resolve();
    };
  }, CLIPBOARD_KEY);
}

/** Ostatnio skopiowany tekst (zob. `prepareClipboard`). */
export async function readClipboard(page: Page): Promise<string | null> {
  if (isChromium(page.context())) return page.evaluate(() => navigator.clipboard.readText());
  return page.evaluate(
    (key) => (window as unknown as Record<string, string | null>)[key] ?? null,
    CLIPBOARD_KEY,
  );
}

const cspViolationsByPage = new WeakMap<Page, string[]>();

/**
 * Zbiera naruszenia CSP niezależnie od silnika. Komunikaty w konsoli mają inny format w Chromium, Firefoksie
 * i WebKicie, więc nasłuchujemy standardowego zdarzenia `securitypolicyviolation` (każdy dokument, także po
 * nawigacji) i przekazujemy je do testu przez `exposeFunction`. Wywołaj przed pierwszym `page.goto`.
 * Zwrócona tablica jest wypełniana na bieżąco: opis w formie `dyrektywa | zablokowany zasób | źródło`.
 */
export async function collectCspViolations(page: Page): Promise<string[]> {
  const existing = cspViolationsByPage.get(page);
  if (existing) return existing; // drugie wywołanie na tej samej stronie: ta sama lista (exposeFunction rzuciłoby)
  const violations: string[] = [];
  cspViolationsByPage.set(page, violations);
  await page.exposeFunction("__e2eReportCspViolation", (description: string) => {
    violations.push(description);
  });
  await page.addInitScript(() => {
    window.addEventListener(
      "securitypolicyviolation",
      (event) => {
        const report = (window as unknown as Record<string, (value: string) => void>)
          .__e2eReportCspViolation;
        report?.(
          `${event.effectiveDirective} | ${event.blockedURI} | ${event.sourceFile || document.URL}`,
        );
      },
      true,
    );
  });
  return violations;
}

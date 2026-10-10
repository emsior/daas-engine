import { expect, type Page } from "@playwright/test";
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

/** Logowanie magic linkiem przez pole „Mam link lub token”. */
export async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Adres e-mail").fill(email);
  await page.getByRole("button", { name: "Wyślij link logowania" }).click();
  await expect(page.getByRole("heading", { name: "Sprawdź skrzynkę" })).toBeVisible();
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

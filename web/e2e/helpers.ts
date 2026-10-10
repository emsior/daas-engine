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
    const match = /token=([A-Za-z0-9_\-.~]+)/.exec(content) ?? /(https?:\/\/\S+)/.exec(content);
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

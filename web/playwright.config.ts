import { defineConfig, devices } from "@playwright/test";

/**
 * E2E uruchamiane przeciw prawdziwemu backendowi w trybie `cloud` z adapterem e-mail `console`.
 * `webServer` jest wyłączony celowo: kokpit i API startuje Integrator (docker compose / dwa procesy).
 *
 * Zmienne środowiskowe:
 *   E2E_BASE_URL    adres kokpitu (domyślnie http://127.0.0.1:3000)
 *   E2E_API_URL     adres API używany przez testy do odczytu linku z outboxa (domyślnie http://127.0.0.1:8000)
 *   E2E_OUTBOX_DIR  katalog runtime/outbox backendu z zapisanymi linkami logowania
 *   E2E_TLS         ustawia stack.sh (=1: proxy po https z jednorazowym certyfikatem); po stronie testów liczy się
 *                   tylko schemat E2E_BASE_URL - dla https:// włączamy ignoreHTTPSErrors (certyfikat self-signed)
 *
 * Projekty: mobile-375 (Pixel 5) i desktop-1280 (Chrome) = Chromium; desktop-firefox = Firefox; mobile-webkit = WebKit
 * (iPhone, viewport 375x667). WebKit odrzuca ciasteczka `Secure`/`__Host-` po http, więc jego przebieg wymaga
 * stosu z E2E_TLS=1 i E2E_BASE_URL=https://127.0.0.1:3100 (produkcja zawsze wystawia `Secure`; nie zmieniamy tego).
 * `npm run e2e` uruchamia projekty Chromium; `npm run e2e:firefox` i `npm run e2e:webkit` osobno (ten drugi wymaga
 * stosu z E2E_TLS=1 i E2E_BASE_URL=https://127.0.0.1:3100).
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    ignoreHTTPSErrors: baseURL.startsWith("https:"),
    trace: "retain-on-failure",
    locale: "pl-PL",
    timezoneId: "Europe/Warsaw",
  },
  projects: [
    { name: "mobile-375", use: { ...devices["Pixel 5"], viewport: { width: 375, height: 667 } } },
    {
      name: "desktop-1280",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
    {
      name: "desktop-firefox",
      use: { ...devices["Desktop Firefox"], viewport: { width: 1280, height: 800 } },
    },
    {
      name: "mobile-webkit",
      use: { ...devices["iPhone 13"], viewport: { width: 375, height: 667 } },
    },
  ],
});

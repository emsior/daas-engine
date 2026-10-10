import { defineConfig, devices } from "@playwright/test";

/**
 * E2E uruchamiane przeciw prawdziwemu backendowi w trybie `cloud` z adapterem e-mail `console`.
 * `webServer` jest wyłączony celowo: kokpit i API startuje Integrator (docker compose / dwa procesy).
 *
 * Zmienne środowiskowe:
 *   E2E_BASE_URL    adres kokpitu (domyślnie http://127.0.0.1:3000)
 *   E2E_API_URL     adres API używany przez testy do odczytu linku z outboxa (domyślnie http://127.0.0.1:8000)
 *   E2E_OUTBOX_DIR  katalog runtime/outbox backendu z zapisanymi linkami logowania
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000",
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
  ],
});

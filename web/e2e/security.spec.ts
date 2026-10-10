import { expect, test } from "@playwright/test";
import { SAMPLE_CSV, UKSC_FIXTURE, runReportFrom, signUp } from "./helpers";

test("CSP: nonce + strict-dynamic, bez unsafe-inline, ramki wyłączone", async ({ request }) => {
  const response = await request.get("/login");
  const csp = response.headers()["content-security-policy"] ?? "";
  expect(csp).toMatch(/script-src [^;]*'nonce-[A-Za-z0-9+/=]+'[^;]*'strict-dynamic'/);
  expect(csp).toMatch(/style-src 'self'(;|$)/);
  expect(csp).not.toContain("unsafe-inline");
  expect(csp).not.toContain("unsafe-eval");
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).toContain("object-src 'none'");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response.headers()["referrer-policy"]).toBe("no-referrer");
});

test("przejście po wszystkich ekranach: zero żądań poza własnym originem, zero naruszeń CSP", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL ?? "http://127.0.0.1:3100").origin;
  const foreign: string[] = [];
  const violations: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (!url.startsWith("data:") && !url.startsWith("blob:") && new URL(url).origin !== origin) {
      foreign.push(`${request.resourceType()} ${url}`);
    }
  });
  page.on("console", (message) => {
    if (/content security policy|refused to (load|apply|execute)/i.test(message.text())) {
      violations.push(message.text());
    }
  });
  page.on("pageerror", (error) => violations.push(error.message));

  await page.goto("/login");
  await signUp(page, "security");
  await runReportFrom(page, SAMPLE_CSV);
  await runReportFrom(page, UKSC_FIXTURE);
  for (const path of [
    "/",
    "/upload",
    "/evidence",
    "/settings",
    "/settings/api-keys",
    "/settings/members",
    "/dpa",
  ]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  }
  await page.goto("/");
  await page
    .getByRole("link", { name: /^Otwórz/ })
    .first()
    .click();
  await expect(page.locator("iframe.iframe-report")).toBeVisible();
  await page.waitForLoadState("networkidle");

  expect(foreign, "żądania do obcych originów (CDN, fonty)").toEqual([]);
  expect(violations, "naruszenia CSP i błędy strony").toEqual([]);
});

test("raport: nagłówki bezpieczeństwa API i ramka w sandboxie bez uprawnień", async ({ page }) => {
  await signUp(page, "report-sec");
  await runReportFrom(page, SAMPLE_CSV);
  await page.getByRole("link", { name: "Otwórz raport" }).click();
  const frame = page.locator("iframe.iframe-report");
  await expect(frame).toHaveAttribute("sandbox", ""); // bez allow-scripts i allow-same-origin
  const src = await frame.getAttribute("src");
  const headers = await page.evaluate(async (url) => {
    const response = await fetch(url ?? "", { credentials: "include" });
    return Object.fromEntries(response.headers.entries());
  }, src);
  expect(headers["content-security-policy"]).toContain("default-src 'none'");
  expect(headers["content-security-policy"]).not.toContain("script-src");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["referrer-policy"]).toBe("no-referrer");
  expect(headers["etag"]).toMatch(/^"[0-9a-f]{64}"$/);
});

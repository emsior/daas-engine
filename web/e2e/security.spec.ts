import { expect, test } from "@playwright/test";
import { SAMPLE_CSV, UKSC_FIXTURE, collectCspViolations, runReportFrom, signUp } from "./helpers";

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
  browserName,
}) => {
  const origin = new URL(baseURL ?? "http://127.0.0.1:3100").origin;
  const foreign: string[] = [];
  const violations: string[] = [];
  // zdarzenie `securitypolicyviolation` działa w każdym silniku; konsola poniżej łapie tylko format Chromium
  const cspEvents = await collectCspViolations(page);
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
  // WebKit: fetch własnego originu przerwany nawigacją (prefetch RSC, odczyt API) kończy się nieobsłużonym
  // odrzuceniem „<url> due to access control checks.” (artefakt silnika). Wyciszamy je tylko z dowodem przerwania:
  // żądanie o tym URL-u wystartowało PRZED późniejszą nawigacją głównej ramki albo ponowieniem tego samego URL-a
  // (nawigacja kliencka zastępuje prefetch) i nie zakończyło się inną porażką
  // niż „cancel”. Kolejność ustalamy po zdarzeniach `request` (jeden kanał, więc uporządkowane); samo
  // requestfailed jest niepełne (Playwright gubi je przy zmianie dokumentu). Rozstrzygamy na końcu testu.
  let seq = 0;
  const started: { url: string; seq: number }[] = [];
  const navigations: number[] = [];
  const failedOtherwise = new Set<string>();
  const accessControlErrors: { message: string; target: string | null }[] = [];
  const host = new URL(origin).host;
  page.on("request", (request) => {
    seq += 1;
    started.push({ url: request.url(), seq });
    if (request.isNavigationRequest() && request.frame() === page.mainFrame())
      navigations.push(seq);
  });
  page.on("requestfailed", (request) => {
    if (!/cancel/i.test(request.failure()?.errorText ?? "")) failedOtherwise.add(request.url());
  });
  page.on("pageerror", (error) => {
    const message = error.message;
    if (browserName === "webkit" && message.endsWith(" due to access control checks.")) {
      const start = message.indexOf(host);
      const target =
        start >= 0 ? message.slice(start, -" due to access control checks.".length) : null;
      accessControlErrors.push({ message, target });
      return;
    }
    violations.push(message);
  });

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
    // WebKit zgłasza fetch przerwany nawigacją (prefetch RSC, odczyty API) jako nieobsłużony błąd strony
    // „... due to access control checks”, a czasem kończy goto błędem wewnętrznym; czekamy na ciszę w sieci
    await page.waitForLoadState("networkidle");
  }
  await page.goto("/");
  await page
    .getByRole("link", { name: /^Otwórz/ })
    .first()
    .click();
  await expect(page.locator("iframe.iframe-report")).toBeVisible();
  await page.waitForLoadState("networkidle");

  for (const { message, target } of accessControlErrors) {
    const matches = target === null ? [] : started.filter(({ url }) => url.endsWith(target));
    const interrupted =
      matches.length > 0 &&
      !matches.some(({ url }) => failedOtherwise.has(url)) &&
      matches.some(
        (entry) =>
          navigations.some((navigation) => navigation > entry.seq) ||
          started.some((later) => later.url === entry.url && later.seq > entry.seq),
      );
    if (!interrupted) violations.push(message); // bez dowodu przerwania liczy się jak każdy inny błąd
  }

  expect(foreign, "żądania do obcych originów (CDN, fonty)").toEqual([]);
  expect(cspEvents, "zdarzenia securitypolicyviolation (wszystkie silniki)").toEqual([]);
  expect(violations, "naruszenia CSP w konsoli i błędy strony").toEqual([]);
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

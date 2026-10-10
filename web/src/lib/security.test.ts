import { describe, expect, it } from "vitest";
import { buildCsp, generateNonce, isPublicPath } from "./security";

describe("CSP", () => {
  const csp = buildCsp({ nonce: "NONCE", apiOrigin: "http://127.0.0.1:8000" });

  it("zawiera nonce i strict-dynamic, bez unsafe-inline w skryptach i stylach", () => {
    expect(csp).toContain("script-src 'self' 'nonce-NONCE' 'strict-dynamic'");
    expect(csp).not.toContain("unsafe-inline");
    expect(csp).not.toContain("unsafe-eval");
    expect(csp).toContain("style-src 'self';");
  });

  it("dopuszcza origin API w connect-src i frame-src", () => {
    expect(csp).toContain("connect-src 'self' http://127.0.0.1:8000");
    expect(csp).toContain("frame-src 'self' http://127.0.0.1:8000");
  });

  it("blokuje osadzanie, base i obce formularze", () => {
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("base-uri 'none'");
    expect(csp).toContain("form-action 'self'");
    expect(csp).toContain("img-src 'self' data:");
  });

  it("przy pustym originie API zostawia tylko 'self'", () => {
    expect(buildCsp({ nonce: "n", apiOrigin: "" })).toContain("connect-src 'self';");
  });

  it("tryb dev dodaje unsafe-eval tylko do skryptów", () => {
    expect(buildCsp({ nonce: "n", apiOrigin: "", isDev: true })).toContain("'unsafe-eval'");
  });

  it("generuje różne nonce", () => {
    expect(generateNonce()).not.toBe(generateNonce());
  });

  it("rozpoznaje ścieżki publiczne", () => {
    expect(isPublicPath("/login")).toBe(true);
    expect(isPublicPath("/dpa")).toBe(true);
    expect(isPublicPath("/upload")).toBe(false);
    expect(isPublicPath("/loginx")).toBe(false);
  });
});

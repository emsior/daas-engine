import { describe, expect, it } from "vitest";
import {
  extractToken,
  isHttpsUrl,
  parseRetentionDays,
  toSummary,
  validateEmail,
  validateOrgName,
} from "./validation";

describe("validation", () => {
  it("waliduje e-mail", () => {
    expect(validateEmail("")).toBe("required");
    expect(validateEmail("abc")).toBe("invalid");
    expect(validateEmail(" ala@example.test ")).toBeNull();
  });

  it("waliduje nazwę organizacji", () => {
    expect(validateOrgName("A")).toBe(false);
    expect(validateOrgName("Firma")).toBe(true);
  });

  it("parsuje retencję 1-3650 dni", () => {
    expect(parseRetentionDays("90")).toBe(90);
    expect(parseRetentionDays("0")).toBeNull();
    expect(parseRetentionDays("3651")).toBeNull();
    expect(parseRetentionDays("12,5")).toBeNull();
    expect(parseRetentionDays("")).toBeNull();
  });

  it("dopuszcza tylko adresy https", () => {
    expect(isHttpsUrl("https://a.example/hook")).toBe(true);
    expect(isHttpsUrl("http://a.example")).toBe(false);
    expect(isHttpsUrl("javascript:alert(1)")).toBe(false);
  });

  it("wyciąga token z linku, ścieżki i gołego tekstu", () => {
    expect(extractToken("http://x.test/login?token=abc123")).toBe("abc123");
    expect(extractToken("http://x.test/verify/abc123")).toBe("abc123");
    expect(extractToken("  abc123 ")).toBe("abc123");
    expect(extractToken("")).toBe("");
  });

  it("składa podsumowanie błędów w kolejności pól", () => {
    expect(toSummary(["b", "a"], { a: "A", b: "B" })).toEqual([
      { id: "b", message: "B" },
      { id: "a", message: "A" },
    ]);
  });
});

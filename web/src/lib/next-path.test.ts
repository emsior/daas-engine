import { afterEach, describe, expect, it } from "vitest";
import { rememberNextPath, safeNextPath, takeRememberedNextPath } from "./next-path";

describe("safeNextPath: tylko ścieżki własnego originu", () => {
  it.each([
    "/oauth/consent?request=01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "/runs/abc",
    "/settings/connected-apps",
  ])("przepuszcza %s", (value) => {
    expect(safeNextPath(value)).toBe(value);
  });

  it.each([
    null,
    "",
    "oauth/consent",
    "https://evil.example.test/",
    "//evil.example.test/path",
    "/\\evil.example.test",
    "/ok\\..\\x",
    "/a\nb",
    "/login",
    "/login?next=/x",
    "/" + "a".repeat(2050),
  ])("odrzuca %s", (value) => {
    expect(safeNextPath(value)).toBeNull();
  });
});

describe("zapamiętany powrót po logowaniu", () => {
  afterEach(() => window.localStorage.clear());

  it("jest jednorazowy", () => {
    rememberNextPath("/oauth/consent?request=X1", 1000);
    expect(takeRememberedNextPath(2000)).toBe("/oauth/consent?request=X1");
    expect(takeRememberedNextPath(2000)).toBeNull();
  });

  it("wygasa i nie zapisuje niebezpiecznych adresów", () => {
    rememberNextPath("/oauth/consent?request=X1", 0);
    expect(takeRememberedNextPath(3 * 60 * 60 * 1000)).toBeNull();
    rememberNextPath("https://evil.example.test/");
    expect(window.localStorage.length).toBe(0);
  });

  it("odrzuca podmienioną wartość w pamięci", () => {
    window.localStorage.setItem(
      "daas.login.next",
      JSON.stringify({ path: "//evil.example.test", until: Date.now() + 10_000 }),
    );
    expect(takeRememberedNextPath()).toBeNull();
    window.localStorage.setItem("daas.login.next", "{not json");
    expect(takeRememberedNextPath()).toBeNull();
  });
});

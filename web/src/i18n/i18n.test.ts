import { describe, expect, it } from "vitest";
import { dictionaries, normalizeLocale, translate, translateDynamic } from "./index";

describe("i18n", () => {
  it("słowniki PL i EN mają identyczny zestaw kluczy", () => {
    expect(Object.keys(dictionaries.en).sort()).toEqual(Object.keys(dictionaries.pl).sort());
  });

  it("żaden tekst nie jest pusty, a znaczniki {…} są takie same w obu językach", () => {
    const placeholders = (text: string) => (text.match(/\{\w+\}/g) ?? []).sort().join(",");
    for (const [key, value] of Object.entries(dictionaries.pl)) {
      const en = (dictionaries.en as Record<string, string>)[key] ?? "";
      expect(value.length, key).toBeGreaterThan(0);
      expect(en.length, key).toBeGreaterThan(0);
      expect(placeholders(en), key).toBe(placeholders(value));
    }
  });

  it("podstawia zmienne", () => {
    expect(translate(dictionaries.pl, "run.title", { date: "09.10.2026" })).toBe(
      "Raport z 09.10.2026",
    );
  });

  it("normalizuje język: domyślnie polski", () => {
    expect(normalizeLocale("en")).toBe("en");
    expect(normalizeLocale("de")).toBe("pl");
    expect(normalizeLocale(undefined)).toBe("pl");
  });

  it("tłumaczenie dynamiczne ma wartość zastępczą", () => {
    expect(translateDynamic(dictionaries.pl, "pipeline.uksc_evidence", "x")).toBe(
      "Raport statusu artefaktów",
    );
    expect(translateDynamic(dictionaries.pl, "pipeline.nieznany", "nieznany")).toBe("nieznany");
  });

  it("w słownikach nie ma zakazanych słów ani zewnętrznych adresów", () => {
    const all = JSON.stringify(dictionaries);
    expect(all).not.toMatch(/https?:\/\/(?!twoja-firma\.pl|your-company\.com)[a-z0-9]/i);
  });
});

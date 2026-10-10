import { describe, expect, it } from "vitest";
import {
  formatBytes,
  formatDate,
  formatDateRange,
  formatDateTime,
  formatNumber,
  formatPLN,
  formatPercent,
} from "./format";

describe("format", () => {
  it("formatuje kwoty z twardymi spacjami: 7 255,95 zł", () => {
    expect(formatPLN(7255.95)).toBe("7 255,95 zł");
  });

  it("grupuje także liczby czterocyfrowe i zachowuje grosze", () => {
    expect(formatPLN(1299)).toBe("1 299,00 zł");
  });

  it("nie używa zwykłej spacji w kwocie", () => {
    expect(formatPLN(1234567.5)).not.toMatch(/ /);
  });

  it("formatuje ujemne kwoty", () => {
    expect(formatPLN(-50)).toContain("50,00");
  });

  it("formatuje daty jako dd.MM.yyyy", () => {
    expect(formatDate("2026-10-09T08:00:00Z")).toBe("09.10.2026");
  });

  it("używa strefy Europe/Warsaw (przełom doby)", () => {
    expect(formatDate("2026-10-08T22:30:00Z")).toBe("09.10.2026");
  });

  it("formatuje datę z godziną", () => {
    expect(formatDateTime("2026-10-09T08:05:00Z")).toBe("09.10.2026 10:05");
  });

  it("zwraca myślnik dla pustej lub niepoprawnej daty", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("to nie data")).toBe("—");
  });

  it("formatuje liczby i procenty po polsku", () => {
    expect(formatNumber(1234567)).toBe("1 234 567");
    expect(formatPercent(12.34)).toBe("12,3 %");
  });

  it("formatuje rozmiary plików", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(10 * 1024 * 1024)).toBe("10 MB");
  });
});

describe("formatDateRange", () => {
  it("formatuje zakres po polsku dd.mm.rrrr", () => {
    expect(formatDateRange("2026-08-03", "2026-08-31")).toBe("03.08.2026 – 31.08.2026");
  });
  it("jedna data, gdy początek = koniec lub znamy tylko jeden koniec", () => {
    expect(formatDateRange("2026-08-03", "2026-08-03")).toBe("03.08.2026");
    expect(formatDateRange("2026-08-03", null)).toBe("03.08.2026");
    expect(formatDateRange(undefined, "2026-08-31")).toBe("31.08.2026");
  });
  it("null tylko gdy brak obu końców (wtedy widok pokazuje „Brak zakresu”)", () => {
    expect(formatDateRange(null, null)).toBeNull();
    expect(formatDateRange(undefined, "")).toBeNull();
  });
});

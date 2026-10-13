import { describe, expect, it } from "vitest";
import type { DataHealthSummary, HealthIssue } from "@/lib/api/types";
import {
  MIN_VISIBLE_PERCENT,
  barPercent,
  barWidthClass,
  formatRunSeconds,
  healthVerdict,
  internalHref,
  isEmptyHealth,
  isoWeekNumber,
  parseIsoDate,
  scaleMax,
  weekRange,
} from "./weekBars";

const NBSP = " ";

describe("skala słupków", () => {
  it("0 → 0 %, maksimum → 100 %, połowa → 50 %", () => {
    expect(barPercent(0, 8)).toBe(0);
    expect(barPercent(8, 8)).toBe(100);
    expect(barPercent(4, 8)).toBe(50);
  });

  it("mała wartość dodatnia jest widoczna, a wartości spoza skali są przycinane", () => {
    expect(barPercent(1, 400)).toBe(MIN_VISIBLE_PERCENT);
    expect(barPercent(9, 8)).toBe(100);
    expect(barPercent(-1, 8)).toBe(0);
    expect(barPercent(3, 0)).toBe(0);
    expect(barPercent(Number.NaN, 8)).toBe(0);
  });

  it("maksimum skali z listy (puste i same zera → 0)", () => {
    expect(scaleMax([1, 4, 2])).toBe(4);
    expect(scaleMax([0, 0])).toBe(0);
    expect(scaleMax([])).toBe(0);
  });

  it("klasa szerokości co 5 %, bez stylu inline (CSP)", () => {
    expect(barWidthClass(0)).toBe("w-0");
    expect(barWidthClass(100)).toBe("w-full");
    expect(barWidthClass(50)).toBe("w-[50%]");
    expect(barWidthClass(52)).toBe("w-[50%]");
    expect(barWidthClass(53)).toBe("w-[55%]");
    expect(barWidthClass(MIN_VISIBLE_PERCENT)).toBe("w-[5%]");
    expect(barWidthClass(140)).toBe("w-full");
    expect(barWidthClass(-3)).toBe("w-0");
    expect(barWidthClass(Number.NaN)).toBe("w-0");
  });
});

describe("etykiety tygodni", () => {
  it("zakres od poniedziałku do niedzieli w trzech wariantach", () => {
    expect(weekRange("2026-10-12")).toBe("12–18.10.2026");
    expect(weekRange("2026-09-28")).toBe("28.09 – 04.10.2026");
    expect(weekRange("2026-12-28")).toBe("28.12.2026 – 03.01.2027");
  });

  it("data z API jest datą lokalną, bez przesunięć strefy przeglądarki", () => {
    expect(parseIsoDate("2026-03-30")?.toISOString()).toBe("2026-03-30T00:00:00.000Z");
    expect(parseIsoDate("2026-02-30")).toBeNull();
    expect(parseIsoDate("30.03.2026")).toBeNull();
    expect(weekRange("nie-data")).toBe("nie-data");
  });

  it("numer tygodnia ISO 8601, także na przełomie roku", () => {
    expect(isoWeekNumber("2026-10-12")).toBe(42);
    expect(isoWeekNumber("2025-12-29")).toBe(1); // 2026 zaczyna się w czwartek
    expect(isoWeekNumber("2026-12-28")).toBe(53);
    expect(isoWeekNumber("2027-01-04")).toBe(1);
    expect(isoWeekNumber("x")).toBeNull();
  });

  it("średni czas uruchomienia", () => {
    expect(formatRunSeconds(null)).toBe("—");
    expect(formatRunSeconds(42)).toBe(`42${NBSP}s`);
    expect(formatRunSeconds(4.54)).toBe(`4,5${NBSP}s`);
    expect(formatRunSeconds(125)).toBe(`2${NBSP}min 5${NBSP}s`);
    expect(formatRunSeconds(-1)).toBe("—");
  });
});

describe("werdykt", () => {
  const summary = { last_success_at: "2026-10-13T08:02:00Z" } as DataHealthSummary;
  const issue = (code: HealthIssue["code"], severity: HealthIssue["severity"]): HealthIssue => ({
    code,
    severity,
    since: null,
    link: "/",
  });

  it("błąd → zagrożony (czerwony), ostrzeżenie → zagrożony (żółty), same wskazówki → bezpieczny", () => {
    expect(
      healthVerdict({
        summary,
        issues: [issue("last_run_failed", "error"), issue("reminder_off", "info")],
      }),
    ).toEqual({ verdict: "at_risk", tone: "error" });
    expect(healthVerdict({ summary, issues: [issue("stale_upload", "warning")] })).toEqual({
      verdict: "at_risk",
      tone: "warn",
    });
    expect(healthVerdict({ summary, issues: [issue("auto_run_off", "info")] })).toEqual({
      verdict: "safe",
      tone: "ok",
    });
    expect(healthVerdict({ summary, issues: [] })).toEqual({ verdict: "safe", tone: "ok" });
  });

  it("pusta organizacja → brak danych, chyba że jest błąd", () => {
    const empty = { last_success_at: null } as DataHealthSummary;
    const data = { summary: empty, issues: [issue("no_uploads_yet", "info")] };
    expect(isEmptyHealth(data)).toBe(true);
    expect(healthVerdict(data)).toEqual({ verdict: "no_data", tone: "warn" });
    expect(
      healthVerdict({ ...data, issues: [...data.issues, issue("plan_limit_reached", "error")] }),
    ).toEqual({ verdict: "at_risk", tone: "error" });
    expect(isEmptyHealth({ summary, issues: data.issues })).toBe(false); // były udane raporty
  });

  it("linki problemów prowadzą wyłącznie do ścieżek kokpitu", () => {
    expect(internalHref("/runs/abc")).toBe("/runs/abc");
    expect(internalHref("/settings")).toBe("/settings");
    expect(internalHref("//evil.example.test/x")).toBe("/");
    expect(internalHref("/\\evil.example.test")).toBe("/");
    expect(internalHref("https://evil.example.test")).toBe("/");
    expect(internalHref("javascript:alert(1)")).toBe("/");
    expect(internalHref("")).toBe("/");
  });
});

import { describe, expect, it } from "vitest";
import type { Run } from "./api/types";
import { runSummary } from "./run-kpis";

function run(metrics: Run["metrics"]): Run {
  return {
    run_id: "r1",
    pipeline: "ecommerce_demo",
    status: "SUCCESS",
    created_at: "2026-10-10T08:00:00Z",
    report_sha256: {},
    metrics,
  };
}

describe("runSummary", () => {
  it("liczby: kwoty w PLN, marża w procentach", () => {
    const items = runSummary(
      run({ revenue_total: 1000, profit_total: 250, margin_pct: 25, avg_order_value: 50 }),
    );
    expect(items.map((item) => item.value)).toEqual([
      "1\u00a0000,00\u00a0zł",
      "250,00\u00a0zł",
      "25,0\u00a0%",
      "50,00\u00a0zł",
    ]);
    expect(items.some((item) => item.undefinedValue)).toBe(false);
  });

  it("margin_pct i avg_order_value równe null (przychód <= 0) dają „n/d”", () => {
    const items = runSummary(
      run({ revenue_total: -20, profit_total: -35, margin_pct: null, avg_order_value: null }),
    );
    const text = items.map((item) => item.value).join(" | ");
    expect(text).not.toMatch(/NaN|Infinity/);
    expect(items[2]).toMatchObject({ value: "n/d", undefinedValue: true });
    expect(items[3]).toMatchObject({ value: "n/d", undefinedValue: true });
    expect(items[0]?.undefinedValue).toBe(false);
  });

  it("używa podanego tekstu zastępczego i pomija pola, których backend nie zwrócił", () => {
    const items = runSummary(run({ margin_pct: null }), "n/a");
    expect(items).toHaveLength(1);
    expect(items[0]?.value).toBe("n/a");
    expect(runSummary(run({}))).toEqual([]);
  });
});

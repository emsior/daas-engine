import type { MessageKey } from "@/i18n";
import { ND, formatNumber, formatPLN, formatPLNOrNd, formatPercentOrNd } from "./format";
import type { Run } from "./api/types";

export interface KpiItem {
  labelKey: MessageKey;
  value: string;
}

const DASH = "—";

function num(metrics: Run["metrics"], key: string): number | null {
  const value = metrics[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function money(value: number | null): string {
  return value === null ? DASH : formatPLN(value);
}

function count(value: number | null): string {
  return value === null ? DASH : formatNumber(value);
}

/** Trzy liczby z etykietami tekstowymi dla karty „Raport tygodnia” (wg rodzaju pipeline'u). */
export function runKpis(run: Run): KpiItem[] {
  const m = run.metrics;
  if (run.pipeline === "uksc_evidence") {
    return [
      { labelKey: "kpi.pass", value: count(num(m, "passed")) },
      { labelKey: "kpi.fail", value: count(num(m, "failed")) },
      { labelKey: "kpi.manual", value: count(num(m, "manual")) },
    ];
  }
  if (run.pipeline === "ecommerce_demo") {
    return [
      { labelKey: "kpi.revenue", value: money(num(m, "revenue_total")) },
      { labelKey: "kpi.profit", value: money(num(m, "profit_total")) },
      { labelKey: "kpi.orders", value: count(num(m, "orders_total")) },
    ];
  }
  return [
    { labelKey: "kpi.records", value: count(run.records_processed ?? null) },
    { labelKey: "kpi.revenue", value: money(num(m, "revenue_total")) },
    { labelKey: "kpi.profit", value: money(num(m, "profit_total")) },
  ];
}

export interface SummaryItem {
  labelKey: MessageKey;
  value: string;
  /** Wartość nieokreślona w danych (np. przychód całkowity <= 0): pokazana jako „n/d”. */
  undefinedValue: boolean;
}

const SUMMARY_FIELDS: { key: string; labelKey: MessageKey; kind: "pln" | "pct" }[] = [
  { key: "revenue_total", labelKey: "kpi.revenue", kind: "pln" },
  { key: "profit_total", labelKey: "kpi.profit", kind: "pln" },
  { key: "margin_pct", labelKey: "run.summary.margin", kind: "pct" },
  { key: "avg_order_value", labelKey: "run.summary.aov", kind: "pln" },
];

/**
 * Podsumowanie runu ze skalarnych metryk. Pokazuje pola, które backend zwrócił (także jako `null`):
 * `margin_pct` i `avg_order_value` są `null` przy przychodzie całkowitym <= 0 (korekty, zwroty),
 * wtedy wartością jest „n/d”, a nie `NaN` ani `0,0 %`.
 */
export function runSummary(run: Run, nd: string = ND): SummaryItem[] {
  const items: SummaryItem[] = [];
  for (const field of SUMMARY_FIELDS) {
    if (!(field.key in run.metrics)) continue;
    const value = num(run.metrics, field.key);
    items.push({
      labelKey: field.labelKey,
      value: field.kind === "pct" ? formatPercentOrNd(value, nd) : formatPLNOrNd(value, nd),
      undefinedValue: value === null,
    });
  }
  return items;
}

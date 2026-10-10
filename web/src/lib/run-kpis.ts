import type { MessageKey } from "@/i18n";
import { formatNumber, formatPLN } from "./format";
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

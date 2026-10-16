"use client";

import { useT } from "@/i18n/provider";
import { cx } from "@/lib/cx";

export type BadgeStatus = "ok" | "warn" | "error" | "pending" | "unknown";

export interface BadgeProps {
  status: BadgeStatus;
  /** Nazwa statusu w tekście; domyślnie z słownika (`badge.<status>`). */
  label?: string;
  className?: string;
}

/** Status = ikona (CSS ::before) + tekst + kolor. Ikona jest ukryta dla czytników. */
export function Badge({ status, label, className }: BadgeProps) {
  const t = useT();
  return (
    <span className={cx("badge", `badge-${status}`, className)}>
      {label ?? t(`badge.${status}`)}
    </span>
  );
}

"use client";

import { useId } from "react";
import { useThrottledValue } from "@/hooks/useThrottledValue";

export interface ProgressProps {
  /** Widoczny tekst postępu, np. „Sprawdzam plik: wiersz 340 z 1 200”. */
  label: string;
  value: number;
  max?: number;
  /** Jak często tekst trafia do regionu aria-live (ms). */
  announceEveryMs?: number;
}

/**
 * `<progress>` + widoczny tekst. Region `aria-live="polite"` jest osobnym, stałym
 * elementem poza podmienianym tekstem i odświeża się rzadko (co `announceEveryMs`).
 */
export function Progress({ label, value, max = 100, announceEveryMs = 2000 }: ProgressProps) {
  const labelId = useId();
  const announced = useThrottledValue(label, announceEveryMs);
  return (
    <div className="stack-2">
      <p id={labelId} className="tabular">
        {label}
      </p>
      <progress className="progress" value={value} max={max} aria-labelledby={labelId} />
      <div role="status" aria-live="polite" className="sr-only">
        {announced}
      </div>
    </div>
  );
}

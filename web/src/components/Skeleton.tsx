import { cx } from "@/lib/cx";

export interface SkeletonProps {
  variant?: "text" | "kpi";
  /** Liczba linii szkieletu tekstu. */
  lines?: number;
  className?: string;
}

/** Szkielet ładowania: dekoracyjny (`aria-hidden`); kontener danych ma `aria-busy="true"`. */
export function Skeleton({ variant = "text", lines = 1, className }: SkeletonProps) {
  return (
    <>
      {Array.from({ length: lines }, (_, index) => (
        <span
          key={index}
          className={cx(
            "skeleton",
            variant === "kpi" ? "skeleton-kpi" : "skeleton-text",
            className,
          )}
          aria-hidden="true"
        />
      ))}
    </>
  );
}

import type { ReactNode } from "react";
import { cx } from "@/lib/cx";

export interface FieldControlProps {
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: true;
  "aria-required"?: true;
}

export interface FieldProps {
  /** Id kontrolki; etykieta, podpowiedź i błąd wyprowadzają z niego swoje id. */
  id: string;
  label: ReactNode;
  hint?: ReactNode;
  /** Komunikat błędu: co poszło źle i jak naprawić. */
  error?: string | null;
  required?: boolean;
  /** Tekst oznaczenia pola opcjonalnego, np. „(opcjonalnie)”. */
  optionalLabel?: string;
  className?: string;
  children: (control: FieldControlProps) => ReactNode;
}

/**
 * Etykieta + podpowiedź + kontrolka + błąd. Kontrolka dostaje `aria-describedby`
 * wskazujące `.hint` i `.field-error` oraz `aria-invalid`, gdy jest błąd.
 */
export function Field({
  id,
  label,
  hint,
  error,
  required,
  optionalLabel,
  className,
  children,
}: FieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={cx("field", className)}>
      <label className="label" htmlFor={id}>
        {label}
        {optionalLabel ? <span className="text-muted"> {optionalLabel}</span> : null}
      </label>
      {hint ? (
        <div className="hint" id={hintId}>
          {hint}
        </div>
      ) : null}
      {children({
        id,
        "aria-describedby": describedBy,
        "aria-invalid": error ? true : undefined,
        "aria-required": required ? true : undefined,
      })}
      {error ? (
        <p className="field-error" id={errorId}>
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
}

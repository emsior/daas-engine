"use client";

import { useEffect, useId, useRef } from "react";

export interface ErrorSummaryItem {
  /** Id pola, do którego prowadzi link. */
  id: string;
  message: string;
}

export interface ErrorSummaryProps {
  title: string;
  intro?: string;
  errors: ErrorSummaryItem[];
  /** Zmiana wartości przenosi fokus ponownie (np. licznik prób wysłania formularza). */
  focusKey?: number | string;
}

/**
 * Podsumowanie błędów na górze `main` (wzorzec GOV.UK). Fokus programowy przy montowaniu
 * i przy zmianie `focusKey`; bez `role="alert"`, żeby czytnik nie ogłaszał dwa razy.
 */
export function ErrorSummary({ title, intro, errors, focusKey }: ErrorSummaryProps) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    ref.current?.focus();
  }, [focusKey]);

  if (errors.length === 0) return null;

  return (
    <div ref={ref} className="error-summary" tabIndex={-1} role="group" aria-labelledby={titleId}>
      <h2 className="error-summary__title" id={titleId}>
        {title}
      </h2>
      {intro ? <p className="mb-3">{intro}</p> : null}
      <ul className="error-summary__list">
        {errors.map((error) => (
          <li key={error.id}>
            <a
              href={`#${error.id}`}
              onClick={(event) => {
                const target = document.getElementById(error.id);
                if (target) {
                  event.preventDefault();
                  target.focus();
                }
              }}
            >
              {error.message}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

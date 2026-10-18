"use client";

import type { ReactNode } from "react";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";

/**
 * Nagłówek h1 widoku. `tabIndex={-1}`: po nawigacji klienckiej fokus trafia tutaj (RouteFocus).
 * Tytuł karty przeglądarki jest ustawiany z tego samego tekstu.
 */
export function PageHeading({ children, lead }: { children: string; lead?: ReactNode }) {
  useDocumentTitle(children);
  return (
    <div className="stack-2">
      <h1 tabIndex={-1}>{children}</h1>
      {lead ? <p className="text-muted">{lead}</p> : null}
    </div>
  );
}

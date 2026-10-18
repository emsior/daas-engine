/** Walidacja pól formularzy (zod). Funkcje zwracają kod błędu; komunikaty pochodzą ze słowników. */
import { z } from "zod";
import "./zod-config";

const emailSchema = z.email();
const retentionSchema = z.coerce.number().int().min(1).max(3650);

export type EmailError = "required" | "invalid";

export function validateEmail(value: string): EmailError | null {
  const trimmed = value.trim();
  if (!trimmed) return "required";
  return emailSchema.safeParse(trimmed).success ? null : "invalid";
}

export function validateOrgName(value: string): boolean {
  const length = value.trim().length;
  return length >= 2 && length <= 120;
}

/** Zwraca liczbę dni albo null, gdy wartość jest niepoprawna. */
export function parseRetentionDays(value: string): number | null {
  if (!value.trim()) return null;
  const result = retentionSchema.safeParse(value.trim());
  return result.success ? result.data : null;
}

export function isHttpsUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    return url.protocol === "https:" && url.hostname.length > 0;
  } catch {
    return false;
  }
}

/**
 * Wyciąga token z wklejonego tekstu: pełny link (`...?token=abc`), końcówka ścieżki
 * (`.../verify/abc`) albo sam token.
 */
export function extractToken(input: string): string {
  const text = input.trim();
  if (!text) return "";
  try {
    const url = new URL(text);
    const fromQuery = url.searchParams.get("token");
    if (fromQuery) return fromQuery;
    const lastSegment = url.pathname.split("/").filter(Boolean).pop();
    if (lastSegment) return lastSegment;
    if (url.hash.length > 1) return url.hash.slice(1);
  } catch {
    // to nie jest adres URL: traktujemy całość jako token
  }
  return text;
}

export interface SummaryItem {
  id: string;
  message: string;
}

/** Buduje listę do ErrorSummary w kolejności pól formularza. */
export function toSummary(
  order: string[],
  errors: Record<string, string | undefined>,
): SummaryItem[] {
  const items: SummaryItem[] = [];
  for (const id of order) {
    const message = errors[id];
    if (message) items.push({ id, message });
  }
  return items;
}

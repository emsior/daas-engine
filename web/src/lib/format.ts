/**
 * Formatowanie liczb, kwot i dat w konwencji polskiej.
 * Kwoty: `7 255,95 zł` z twardymi spacjami (U+00A0), niezależnie od wersji ICU.
 * Daty: `dd.MM.yyyy` w strefie Europe/Warsaw.
 */

const NBSP = " ";

const currencyFormat = new Intl.NumberFormat("pl-PL", {
  style: "currency",
  currency: "PLN",
  useGrouping: "always", // pl-PL domyślnie nie grupuje liczb 4-cyfrowych (minimumGroupingDigits=2)
});

const numberFormat = new Intl.NumberFormat("pl-PL", {
  useGrouping: "always",
  maximumFractionDigits: 2,
});

const percentFormat = new Intl.NumberFormat("pl-PL", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** Zamienia dowolne spacje (zwykłe, wąskie U+202F) na twardą spację. */
function hardSpaces(value: string): string {
  return value.replace(/[  ]/g, NBSP);
}

export function formatPLN(value: number): string {
  return hardSpaces(currencyFormat.format(value));
}

export function formatNumber(value: number): string {
  return hardSpaces(numberFormat.format(value));
}

export function formatPercent(value: number): string {
  return `${hardSpaces(percentFormat.format(value))}${NBSP}%`;
}

/** Wartość nieokreślona (np. marża przy przychodzie całkowitym <= 0): „n/d”, nigdy `NaN` ani `0,0 %`. */
export const ND = "n/d";

function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatPercentOrNd(value: number | null | undefined, nd: string = ND): string {
  return isFiniteNumber(value) ? formatPercent(value) : nd;
}

export function formatPLNOrNd(value: number | null | undefined, nd: string = ND): string {
  return isFiniteNumber(value) ? formatPLN(value) : nd;
}

const dateParts = new Intl.DateTimeFormat("pl-PL", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Europe/Warsaw",
});

function parts(date: Date): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of dateParts.formatToParts(date)) {
    out[p.type] = p.value;
  }
  return out;
}

function toDate(input: string | number | Date): Date | null {
  const date = input instanceof Date ? input : new Date(input);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** `09.10.2026`; dla niepoprawnej daty zwraca myślnik. */
export function formatDate(input: string | number | Date | null | undefined): string {
  if (input === null || input === undefined || input === "") return "—";
  const date = toDate(input);
  if (!date) return "—";
  const p = parts(date);
  return `${p.day}.${p.month}.${p.year}`;
}

/**
 * Zakres dat `03.08.2026 – 31.08.2026`; jedna data, gdy początek i koniec są tym samym dniem lub znamy tylko jeden.
 * Zwraca `null`, gdy brak obu końców (wołający pokazuje wtedy „Brak zakresu”).
 */
export function formatDateRange(
  from: string | null | undefined,
  to: string | null | undefined,
): string | null {
  const start = from ? formatDate(from) : null;
  const end = to ? formatDate(to) : null;
  if (!start && !end) return null;
  if (!start || !end || start === end) return start ?? end;
  return `${start} – ${end}`;
}

/** `09.10.2026 14:05`. */
export function formatDateTime(input: string | number | Date | null | undefined): string {
  if (input === null || input === undefined || input === "") return "—";
  const date = toDate(input);
  if (!date) return "—";
  const p = parts(date);
  return `${p.day}.${p.month}.${p.year} ${p.hour}:${p.minute}`;
}

/** Rozmiar w bajtach jako czytelny tekst: `1,5 MB`. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${formatNumber(bytes)}${NBSP}B`;
  const units = ["KB", "MB", "GB"] as const;
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${hardSpaces(new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 1 }).format(value))}${NBSP}${units[unit]}`;
}

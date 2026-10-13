/**
 * Czyste funkcje pulpitu „Zdrowie danych”: skala słupków w tabeli tygodni, etykiety tygodni, czas uruchomienia
 * i werdykt „raport w przyszły poniedziałek”. Bez Reacta i bez zależności od strefy przeglądarki.
 */
import type { BadgeStatus } from "@/components/Badge";
import type { DataHealth, HealthIssue } from "@/lib/api/types";
import { formatNumber } from "@/lib/format";

const DAY_MS = 86_400_000;
const NBSP = " ";

/* ------------------------------------------------------------------ */
/* Słupki                                                              */
/* ------------------------------------------------------------------ */

/** Niezerowa wartość dostaje co najmniej tyle procent, żeby słupek był widoczny. */
export const MIN_VISIBLE_PERCENT = 5;

/** Szerokość słupka w procentach maksimum skali: 0 → 0 %, max → 100 %, wartość dodatnia → co najmniej 5 %. */
export function barPercent(value: number, max: number): number {
  if (!(value > 0) || !(max > 0)) return 0;
  return Math.min(100, Math.max(MIN_VISIBLE_PERCENT, Math.round((value / max) * 100)));
}

/** Maksimum skali słupków (0 dla pustej listy albo samych zer). */
export function scaleMax(values: readonly number[]): number {
  return values.reduce((max, value) => (value > max ? value : max), 0);
}

/**
 * Klasy szerokości co 5 %. CSP kokpitu nie ma `'unsafe-inline'` w `style-src`, więc szerokości nie ustawiamy
 * przez `style=""`: literały poniżej są w źródle, żeby Tailwind wygenerował dla nich reguły.
 */
const WIDTH_CLASSES = [
  "w-0",
  "w-[5%]",
  "w-[10%]",
  "w-[15%]",
  "w-[20%]",
  "w-[25%]",
  "w-[30%]",
  "w-[35%]",
  "w-[40%]",
  "w-[45%]",
  "w-[50%]",
  "w-[55%]",
  "w-[60%]",
  "w-[65%]",
  "w-[70%]",
  "w-[75%]",
  "w-[80%]",
  "w-[85%]",
  "w-[90%]",
  "w-[95%]",
  "w-full",
] as const;

/** Klasa Tailwinda dla szerokości `percent` (zaokrąglenie do 5 %). */
export function barWidthClass(percent: number): string {
  const clamped = Number.isFinite(percent) ? Math.min(100, Math.max(0, percent)) : 0;
  return WIDTH_CLASSES[Math.round(clamped / 5)] ?? "w-0";
}

/* ------------------------------------------------------------------ */
/* Etykiety tygodni                                                    */
/* ------------------------------------------------------------------ */

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** `YYYY-MM-DD` (data lokalna z API, bez strefy) → północ UTC tego dnia; inny format → null. */
export function parseIsoDate(value: string): Date | null {
  const match = ISO_DATE.exec(value);
  if (!match) return null;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCMonth() === month - 1 && date.getUTCDate() === day ? date : null;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** Zakres tygodnia od poniedziałku: `12–18.10.2026`, `28.09 – 04.10.2026`, `28.12.2026 – 03.01.2027`. */
export function weekRange(weekStart: string): string {
  const start = parseIsoDate(weekStart);
  if (!start) return weekStart;
  const end = new Date(start.getTime() + 6 * DAY_MS);
  const [sd, sm, sy] = [start.getUTCDate(), start.getUTCMonth() + 1, start.getUTCFullYear()];
  const [ed, em, ey] = [end.getUTCDate(), end.getUTCMonth() + 1, end.getUTCFullYear()];
  if (sy !== ey) return `${pad(sd)}.${pad(sm)}.${sy} – ${pad(ed)}.${pad(em)}.${ey}`;
  if (sm !== em) return `${pad(sd)}.${pad(sm)} – ${pad(ed)}.${pad(em)}.${ey}`;
  return `${pad(sd)}–${pad(ed)}.${pad(em)}.${ey}`;
}

/** Numer tygodnia ISO 8601 (tydzień należy do roku, w którym wypada jego czwartek). */
export function isoWeekNumber(day: string): number | null {
  const date = parseIsoDate(day);
  if (!date) return null;
  const mondayOffset = (date.getUTCDay() + 6) % 7;
  const thursday = new Date(date.getTime() + (3 - mondayOffset) * DAY_MS);
  const yearStart = Date.UTC(thursday.getUTCFullYear(), 0, 1);
  return Math.floor((thursday.getTime() - yearStart) / DAY_MS / 7) + 1;
}

/** Średni czas uruchomienia: `42 s`, `4,5 s`, `2 min 5 s`; brak danych → `—`. */
export function formatRunSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${formatNumber(Math.round(seconds * 10) / 10)}${NBSP}s`;
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}${NBSP}min ${total % 60}${NBSP}s`;
}

/* ------------------------------------------------------------------ */
/* Werdykt i linki                                                     */
/* ------------------------------------------------------------------ */

export type Verdict = "safe" | "at_risk" | "no_data";

/** Pusta organizacja: nie było żadnego pliku i żadnego udanego uruchomienia. */
export function isEmptyHealth(data: Pick<DataHealth, "issues" | "summary">): boolean {
  return (
    data.issues.some((issue) => issue.code === "no_uploads_yet") &&
    data.summary.last_success_at === null
  );
}

/**
 * Werdykt z listy problemów: błąd → zagrożony (czerwony), pusta organizacja → brak danych (żółty),
 * ostrzeżenie → zagrożony (żółty), same wskazówki albo nic → bezpieczny (zielony).
 */
export function healthVerdict(data: Pick<DataHealth, "issues" | "summary">): {
  verdict: Verdict;
  tone: BadgeStatus;
} {
  const has = (severity: HealthIssue["severity"]) =>
    data.issues.some((issue) => issue.severity === severity);
  if (has("error")) return { verdict: "at_risk", tone: "error" };
  if (isEmptyHealth(data)) return { verdict: "no_data", tone: "warn" };
  if (has("warning")) return { verdict: "at_risk", tone: "warn" };
  return { verdict: "safe", tone: "ok" };
}

/** Link z API jest ścieżką kokpitu; wszystko inne (pełny adres, `//host`) zamieniamy na pulpit. */
export function internalHref(link: string): string {
  return /^\/(?![/\\])/.test(link) ? link : "/";
}

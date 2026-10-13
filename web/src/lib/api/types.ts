/**
 * Typy kontraktu API v1 (SPEC_v2.md, sekcja 2.4), napisane ręcznie.
 *
 * Docelowo typy pochodzą z `docs/openapi.json` (`npm run gen:api` -> `schema.d.ts`).
 * Wtedy w tym pliku wystarczy zastąpić definicje aliasami, np.:
 *   import type { components } from "./schema";
 *   export type Org = components["schemas"]["OrgOut"];
 * Reszta aplikacji importuje typy wyłącznie stąd, a ścieżki żyją tylko w `client.ts`.
 */

export type Locale = "pl" | "en";
export type Role = "owner" | "admin" | "member";
export type Plan = "free" | "pro" | "selfhost";

export interface User {
  id: string;
  email: string;
  name: string | null;
  locale: Locale;
}

export interface Org {
  id: string;
  slug: string;
  name: string;
  plan: Plan;
  locale: Locale;
  dpa_accepted_at: string | null;
  dpa_version: string | null;
  retention_days: number;
  llm_narrative: boolean;
  created_at: string;
}

/** `GET /me`: zalogowany użytkownik i jego aktywna organizacja (null przed onboardingiem). */
export interface Me {
  user: User;
  org: Org | null;
  role: Role | null;
}

export interface MagicLinkRequest {
  email: string;
}

export interface MagicLinkVerifyRequest {
  token: string;
}

export interface CreateOrgRequest {
  name: string;
  locale: Locale;
  /** Akceptacja umowy powierzenia (DPA); backend wymaga `true`. */
  dpa_accepted: boolean;
  dpa_version: "v1";
}

export interface UpdateOrgRequest {
  retention_days?: number;
  llm_narrative?: boolean;
}

/** Pipeline'y, które kokpit umie uruchomić (`JobCreate.pipeline`). */
export type KnownPipeline = "ecommerce_demo" | "uksc_evidence" | "cs2_demo";
export type PipelineName = KnownPipeline | (string & {});

/** Odpowiedź `POST /orgs/{id}/uploads`: wykryty mapping kolumn (kanoniczna -> nagłówek z pliku). */
export interface UploadPreview {
  upload_id: string;
  filename: string;
  kind?: "csv" | "xlsx" | "package";
  bytes?: number;
  rows: number;
  columns: string[];
  mapping: Record<string, string>;
  unmapped_columns: string[];
  generated: string[];
  warnings: string[];
  confidence: number;
  ready: boolean;
  /** Pola brakujące wymaganego mappingu (jeśli backend je zwraca). */
  missing_columns?: string[];
  suggested_pipeline?: KnownPipeline;
  /** Zadanie zakolejkowane przez API zaraz po uploadzie (harmonogram: auto-uruchamianie); null = brak. */
  auto_job_id?: string | null;
}

export interface CreateJobRequest {
  pipeline: KnownPipeline;
  upload_id: string;
  /** Zamówiony okres raportu (ISO `YYYY-MM-DD`, włącznie; tylko `ecommerce_demo`); pominięty = bez granicy. */
  period_from?: string | null;
  period_to?: string | null;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Job {
  id: string;
  pipeline: PipelineName;
  status: JobStatus;
  progress: number;
  message: string | null;
  run_id: string | null;
  upload_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  /** Okres zamówiony przy starcie zadania (ISO `YYYY-MM-DD`, włącznie); null = bez granicy z tej strony. */
  period_from?: string | null;
  period_to?: string | null;
}

/** Zdarzenia SSE z `GET /orgs/{id}/jobs/{job_id}/events`. */
export interface JobStatusEvent {
  status: JobStatus;
  /** Migawka zadania zawiera też aktualny postęp (0-100). */
  progress?: number;
}
export interface JobProgressEvent {
  progress: number;
  /** Przetworzone wiersze (opcjonalnie) i ich łączna liczba. */
  rows_done?: number;
  rows_total?: number;
  message?: string;
}
export interface JobLogEvent {
  message: string;
}
export interface JobDoneEvent {
  status: JobStatus;
  run_id?: string | null;
  error_code?: string | null;
}

export type RunStatus = "SUCCESS" | "PARTIAL" | "FAILED";

export interface Run {
  run_id: string;
  pipeline: PipelineName;
  status: RunStatus;
  created_at: string;
  finished_at?: string | null;
  records_processed?: number;
  /** Zakres danych, których dotyczy raport (jeśli backend go podaje). */
  period_from?: string | null;
  period_to?: string | null;
  /** Skróty SHA-256 plików raportu (hex, 64 znaki). */
  report_sha256: { html?: string; md?: string; json?: string };
  /** Kompaktowe metryki z runu (np. revenue_total, profit_total, orders_total). */
  metrics: Record<string, number | string | boolean | null>;
}

export type ReportFormat = "html" | "md" | "json";

export type CheckStatus = "PASS" | "FAIL" | "WARN" | "MANUAL" | "NA_NO_ADMIN" | "ERROR";

export interface EvidenceControl {
  control_id: string;
  title: string;
  /** Wymóg, np. `art. 8 ust. 1 pkt 2 lit. k`. */
  requirement: string;
  status: CheckStatus;
  artifact: {
    sha256: string | null;
    collected_at: string | null;
    source?: string | null;
  } | null;
  collector_command?: string | null;
}

/** `GET /orgs/{id}/evidence`: status kontroli z ostatniego runu `uksc_evidence`. */
export interface EvidenceReport {
  run_id: string | null;
  /** Status runu kolektora; `PARTIAL` = niepełna paczka (część kontroli bez artefaktu). */
  run_status?: RunStatus | null;
  collected_at: string | null;
  host?: string | null;
  collector_command: string | null;
  controls: EvidenceControl[];
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** Odpowiedź tworzenia: pełny klucz jest zwracany tylko raz. */
export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export interface CreateApiKeyRequest {
  name: string;
  scopes: string[];
  /** Liczba dni ważności; null = bez wygaśnięcia. */
  expires_in_days: number | null;
}

/** Zakresy nadawane kluczom (podzbiór `KEY_SCOPES` backendu, bez uprawnień administracyjnych poza kluczami). */
export const API_KEY_SCOPES = [
  "uploads:write",
  "runs:write",
  "runs:read",
  "reports:read",
  "usage:read",
  "keys:manage",
] as const;
export type ApiKeyScope = (typeof API_KEY_SCOPES)[number];

export interface Member {
  user_id: string;
  email: string;
  name: string | null;
  role: Role;
  created_at?: string | null;
}

export interface InviteRequest {
  email: string;
  role: Role;
}

/** Oczekujące zaproszenie (`GET /orgs/{id}/invitations`). */
export interface Invitation {
  id: string;
  email: string;
  role: Role;
  created_at: string;
  expires_at: string;
}

export interface Webhook {
  id: string;
  url: string;
  events: string[];
  active: boolean;
  created_at?: string;
  /** Sekret podpisu HMAC: zwracany tylko przy tworzeniu. */
  secret?: string;
}

export type WebhookEvent = "run.finished";

export interface CreateWebhookRequest {
  url: string;
  events: WebhookEvent[];
}

export type PingStatusClass = "2xx" | "3xx" | "4xx" | "5xx" | "error";

/** Wynik testu webhooka: backend podaje klasę odpowiedzi, nie dokładny kod. */
export interface WebhookPingResult {
  ok: boolean;
  status_class: PingStatusClass;
}

/** Format wiadomości na czacie zespołu po runie (`notify_webhook_kind`): Slack, Microsoft Teams albo zwykły JSON. */
export type ChatWebhookKind = "slack" | "teams" | "generic";

/** Harmonogram i powiadomienia organizacji (`GET/PUT /orgs/{id}/schedule`). Godzina jest lokalna w `timezone`. */
export interface ScheduleSettings {
  reminder_enabled: boolean;
  /** 0 = poniedziałek ... 6 = niedziela. */
  reminder_weekday: number;
  /** 0-23. */
  reminder_hour: number;
  /** Strefa IANA, np. `Europe/Warsaw` (walidacja po stronie API). */
  timezone: string;
  reminder_skip_if_recent_upload: boolean;
  auto_run_on_upload: boolean;
  notify_on_finish: boolean;
  /** Format powiadomienia na czacie; pominięty w PUT = `slack` (PUT zastępuje cały harmonogram). */
  notify_webhook_kind?: ChatWebhookKind;
  /**
   * Adres webhooka czatu, tylko do zapisu (API nigdy go nie zwraca): pominięty = bez zmian,
   * `null` = usuń zapisany adres, tekst = zapisz nowy (https, adres publiczny).
   */
  notify_webhook_url?: string | null;
}

export interface Schedule extends Omit<
  ScheduleSettings,
  "notify_webhook_kind" | "notify_webhook_url"
> {
  notify_webhook_kind: ChatWebhookKind;
  /** Czy zapisano adres webhooka czatu. */
  notify_webhook_set: boolean;
  /** Zamaskowany zapisany adres, np. `hooks.slack.com/…XyZ9`; null = brak adresu. */
  notify_webhook_hint: string | null;
  /** Najbliższe przypomnienie (UTC, ISO); null = przypomnienie wyłączone. */
  next_reminder_at: string | null;
  last_reminder_at: string | null;
  /** null = wartości domyślne, nigdy niezapisane. */
  updated_at: string | null;
}

export interface Usage {
  period: string;
  runs: number;
  upload_bytes: number;
  runs_limit?: number | null;
  upload_bytes_limit?: number | null;
}

/* --- pulpit „Zdrowie danych” (`GET /orgs/{id}/data-health`) --- */

export type HealthIssueCode =
  | "last_run_failed"
  | "plan_limit_reached"
  | "stale_upload"
  | "plan_limit_near"
  | "partial_reports"
  | "no_uploads_yet"
  | "reminder_off"
  | "auto_run_off";
export type HealthSeverity = "error" | "warning" | "info";

/** Jeden tydzień ISO w strefie harmonogramu organizacji (poniedziałek 00:00 czasu lokalnego). */
export interface WeekHealth {
  /** Poniedziałek tygodnia: data lokalna `YYYY-MM-DD` (bez godziny i strefy). */
  week_start: string;
  /** Bieżący, jeszcze trwający tydzień. */
  partial: boolean;
  uploads: number;
  upload_bytes: number;
  runs_succeeded: number;
  /** Zadania zakończone błędem, w tym `no_records`. */
  runs_failed: number;
  /** Raporty SUCCESS i PARTIAL, których zadanie nie zakończyło się błędem. */
  reports: number;
  /** Średni czas uruchomienia (sekundy); null = brak zakończonych zadań. */
  avg_run_seconds: number | null;
  last_report_at: string | null;
}

export interface DataHealthSummary {
  last_upload_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error_code: string | null;
  /** Pełne tygodnie z rzędu z co najmniej jednym raportem (bez bieżącego). */
  streak_weeks: number;
  reminder_enabled: boolean;
  next_reminder_at: string | null;
  auto_run_on_upload: boolean;
  notify_on_finish: boolean;
  plan: string;
  runs_used: number;
  /** null = plan bez limitu. */
  runs_limit: number | null;
  retention_days: number;
}

export interface HealthIssue {
  code: HealthIssueCode;
  severity: HealthSeverity;
  since: string | null;
  /** Ścieżka w kokpicie, która wyjaśnia albo naprawia problem (np. `/upload`). */
  link: string;
}

/** Problemy są posortowane od najpoważniejszych; tygodnie od najstarszego do bieżącego. */
export interface DataHealth {
  generated_at: string;
  /** Strefa IANA harmonogramu, w której liczone są tygodnie. */
  timezone: string;
  weeks: WeekHealth[];
  summary: DataHealthSummary;
  issues: HealthIssue[];
}

export interface VerifyResult {
  known: boolean;
  run_id?: string | null;
  created_at?: string | null;
  pipeline?: PipelineName | null;
  /** Format pliku raportu, do którego pasuje skrót. */
  format?: ReportFormat | null;
}

/** Ciało błędu `application/problem+json` (RFC 9457) z rozszerzeniem `request_id`. */
export interface ProblemDetails {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  instance?: string;
  request_id?: string;
  code?: string;
  /** `confirm_required`: zamaskowany adres, na który zamówiono link logowania. */
  email_hint?: string;
  /** Błędy pól: `{loc, msg}` (walidacja FastAPI) albo `{field, message}`. */
  errors?: { loc?: string[]; msg?: string; field?: string; message?: string }[];
}

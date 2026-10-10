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
}

export interface CreateJobRequest {
  pipeline: KnownPipeline;
  upload_id: string;
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

export interface Invitation {
  id?: string;
  email: string;
  role: Role;
  expires_at?: string | null;
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

export interface WebhookPingResult {
  ok: boolean;
  status_code?: number | null;
}

export interface Usage {
  period: string;
  runs: number;
  upload_bytes: number;
  runs_limit?: number | null;
  upload_bytes_limit?: number | null;
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
  /** Błędy pól: `{loc, msg}` (walidacja FastAPI) albo `{field, message}`. */
  errors?: { loc?: string[]; msg?: string; field?: string; message?: string }[];
}

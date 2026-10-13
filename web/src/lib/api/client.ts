/**
 * Klient API v1. Jedno miejsce mapowania ścieżek (`paths`) i jedno miejsce obsługi błędów.
 * - `credentials: "include"` (sesja w ciasteczku HttpOnly),
 * - nagłówek `X-CSRF-Token` z ciasteczka `daas_csrf` dla metod zmieniających stan,
 * - błędy `application/problem+json` -> `ApiError`.
 */
import { CSRF_COOKIE, CSRF_HEADER, apiUrl } from "../env";
import { readCookie } from "../cookies";
import type {
  ApiKey,
  ApiKeyCreated,
  CreateApiKeyRequest,
  CreateJobRequest,
  CreateOrgRequest,
  CreateWebhookRequest,
  EvidenceReport,
  Invitation,
  InviteRequest,
  Job,
  Locale,
  Member,
  Me,
  Org,
  ProblemDetails,
  Role,
  ReportFormat,
  Run,
  Schedule,
  ScheduleSettings,
  UpdateOrgRequest,
  UploadPreview,
  Usage,
  VerifyResult,
  Webhook,
  WebhookPingResult,
} from "./types";
import {
  itemsOf,
  toEvidence,
  toInvitation,
  toMe,
  toRun,
  toUploadPreview,
  toUsage,
  toWebhook,
  toWebhookPing,
  type WireEvidence,
  type WireInvitation,
  type WireMe,
  type WirePage,
  type WireRun,
  type WireRunDetail,
  type WireSession,
  type WireUpload,
  type WireUsage,
  type WireWebhook,
  type WireWebhookPing,
} from "./wire";

/* ------------------------------------------------------------------ */
/* Błędy                                                               */
/* ------------------------------------------------------------------ */

export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string | null;
  readonly requestId: string | null;
  readonly code: string | null;
  readonly emailHint: string | null;
  readonly fieldErrors: { field?: string; message: string }[];

  constructor(init: {
    status: number;
    title: string;
    detail?: string | null;
    requestId?: string | null;
    code?: string | null;
    emailHint?: string | null;
    fieldErrors?: { field?: string; message: string }[];
  }) {
    super(init.detail ? `${init.title}: ${init.detail}` : init.title);
    this.name = "ApiError";
    this.status = init.status;
    this.title = init.title;
    this.detail = init.detail ?? null;
    this.requestId = init.requestId ?? null;
    this.code = init.code ?? null;
    this.emailHint = init.emailHint ?? null;
    this.fieldErrors = init.fieldErrors ?? [];
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }
  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/* ------------------------------------------------------------------ */
/* Ścieżki (jedyne miejsce, które trzeba zmienić po regeneracji typów) */
/* ------------------------------------------------------------------ */

export const paths = {
  me: () => "/me",
  magicLink: () => "/auth/magic-link",
  magicLinkVerify: () => "/auth/magic-link/verify",
  logout: () => "/auth/logout",
  orgs: () => "/orgs",
  org: (org: string) => `/orgs/${org}`,
  members: (org: string) => `/orgs/${org}/members`,
  invitations: (org: string) => `/orgs/${org}/invitations`,
  invitation: (org: string, id: string) => `/orgs/${org}/invitations/${id}`,
  member: (org: string, userId: string) => `/orgs/${org}/members/${userId}`,
  apiKeys: (org: string) => `/orgs/${org}/api-keys`,
  apiKey: (org: string, id: string) => `/orgs/${org}/api-keys/${id}`,
  uploads: (org: string) => `/orgs/${org}/uploads`,
  jobs: (org: string) => `/orgs/${org}/jobs`,
  job: (org: string, id: string) => `/orgs/${org}/jobs/${id}`,
  jobEvents: (org: string, id: string) => `/orgs/${org}/jobs/${id}/events`,
  jobCancel: (org: string, id: string) => `/orgs/${org}/jobs/${id}/cancel`,
  runs: (org: string) => `/orgs/${org}/runs`,
  run: (org: string, id: string) => `/orgs/${org}/runs/${id}`,
  runReport: (org: string, id: string) => `/orgs/${org}/runs/${id}/report`,
  verify: (org: string) => `/orgs/${org}/verify`,
  evidence: (org: string) => `/orgs/${org}/evidence`,
  usage: (org: string) => `/orgs/${org}/usage`,
  schedule: (org: string) => `/orgs/${org}/schedule`,
  webhooks: (org: string) => `/orgs/${org}/webhooks`,
  webhook: (org: string, id: string) => `/orgs/${org}/webhooks/${id}`,
  webhookPing: (org: string, id: string) => `/orgs/${org}/webhooks/${id}/ping`,
} as const;

/* ------------------------------------------------------------------ */
/* Transport                                                           */
/* ------------------------------------------------------------------ */

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions {
  method?: Method;
  /** Ciało JSON. */
  json?: unknown;
  /** Ciało multipart (upload). */
  form?: FormData;
  query?: Record<string, string | number | boolean | undefined>;
  signal?: AbortSignal;
}

const SAFE_METHODS = new Set<Method>(["GET"]);

function buildQuery(query: RequestOptions["query"]): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

async function toApiError(response: Response): Promise<ApiError> {
  const headerId = response.headers.get("x-request-id");
  let problem: ProblemDetails | null = null;
  try {
    const text = await response.text();
    if (text) {
      const parsed: unknown = JSON.parse(text);
      if (parsed && typeof parsed === "object") problem = parsed as ProblemDetails;
    }
  } catch {
    problem = null;
  }
  return new ApiError({
    status: response.status,
    title: problem?.title ?? (response.statusText || `HTTP ${response.status}`),
    detail: problem?.detail ?? null,
    requestId: problem?.request_id ?? headerId,
    code: problem?.code ?? null,
    emailHint: problem?.email_hint ?? null,
    fieldErrors: (problem?.errors ?? []).map((entry) => ({
      field: entry.field ?? entry.loc?.filter((part) => part !== "body").join("."),
      message: entry.message ?? entry.msg ?? "",
    })),
  });
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json, application/problem+json" };
  let body: BodyInit | undefined;

  if (options.form) {
    body = options.form; // przeglądarka sama ustawi boundary
  } else if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }

  if (!SAFE_METHODS.has(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers[CSRF_HEADER] = csrf;
  }

  let response: Response;
  try {
    response = await fetch(`${apiUrl(path)}${buildQuery(options.query)}`, {
      method,
      headers,
      body,
      credentials: "include",
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError({
      status: 0,
      title: "network_error",
      detail: cause instanceof Error ? cause.message : null,
    });
  }

  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;

  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError({
      status: response.status,
      title: "invalid_response",
      requestId: response.headers.get("x-request-id"),
    });
  }
}

/* ------------------------------------------------------------------ */
/* Metody typowane                                                     */
/* ------------------------------------------------------------------ */

export const api = {
  /** `GET /me` (sesje i organizacje) + `GET /orgs/{id}` (pełna organizacja aktywna = pierwsza z listy). */
  me: async (signal?: AbortSignal): Promise<Me> => {
    const wire = await request<WireMe>(paths.me(), { signal });
    const first = wire.orgs[0];
    const org = first ? await request<Org>(paths.org(first.org_id), { signal }) : null;
    return toMe(wire, org);
  },
  requestMagicLink: (email: string, locale?: Locale) =>
    request<void>(paths.magicLink(), {
      method: "POST",
      json: { email, ...(locale ? { locale } : {}) },
    }),
  /** Wymiana tokenu na sesję (cookie); odpowiedź backendu to sesja, więc profil pobieramy przez `me`. */
  verifyMagicLink: async (token: string, confirm = false): Promise<Me> => {
    await request<WireSession>(paths.magicLinkVerify(), {
      method: "POST",
      json: confirm ? { token, confirm: true } : { token },
    });
    return api.me();
  },
  logout: () => request<void>(paths.logout(), { method: "POST" }),

  createOrg: (body: CreateOrgRequest) => request<Org>(paths.orgs(), { method: "POST", json: body }),
  getOrg: (org: string) => request<Org>(paths.org(org)),
  updateOrg: (org: string, body: UpdateOrgRequest) =>
    request<Org>(paths.org(org), { method: "PATCH", json: body }),

  listMembers: (org: string) => request<Member[]>(paths.members(org)),
  /** Zaproszenie: backend zawsze odpowiada 202 `{status: "accepted"}` (nie zdradza, czy adres ma konto). */
  invite: (org: string, body: InviteRequest) =>
    request<void>(paths.invitations(org), { method: "POST", json: body }),
  listInvitations: async (org: string): Promise<Invitation[]> =>
    (await request<WireInvitation[]>(paths.invitations(org))).map(toInvitation),
  revokeInvitation: (org: string, id: string) =>
    request<void>(paths.invitation(org, id), { method: "DELETE" }),
  changeMemberRole: (org: string, userId: string, role: Role) =>
    request<Member>(paths.member(org, userId), { method: "PATCH", json: { role } }),
  removeMember: (org: string, userId: string) =>
    request<void>(paths.member(org, userId), { method: "DELETE" }),

  listApiKeys: (org: string) => request<ApiKey[]>(paths.apiKeys(org)),
  createApiKey: (org: string, body: CreateApiKeyRequest) =>
    request<ApiKeyCreated>(paths.apiKeys(org), { method: "POST", json: body }),
  revokeApiKey: (org: string, id: string) =>
    request<void>(paths.apiKey(org, id), { method: "DELETE" }),

  upload: async (org: string, file: File, signal?: AbortSignal): Promise<UploadPreview> => {
    const form = new FormData();
    form.append("file", file);
    const wire = await request<WireUpload>(paths.uploads(org), { method: "POST", form, signal });
    return toUploadPreview(wire);
  },
  createJob: (org: string, body: CreateJobRequest) =>
    request<Job>(paths.jobs(org), { method: "POST", json: body }),
  getJob: (org: string, id: string, signal?: AbortSignal) =>
    request<Job>(paths.job(org, id), { signal }),
  cancelJob: (org: string, id: string) =>
    request<Job>(paths.jobCancel(org, id), { method: "POST" }),

  listRuns: async (org: string, limit = 50): Promise<Run[]> => {
    const page = await request<WirePage<WireRun> | WireRun[]>(paths.runs(org), {
      query: { limit },
    });
    return itemsOf(page).map(toRun);
  },
  getRun: async (org: string, id: string): Promise<Run> =>
    toRun(await request<WireRunDetail>(paths.run(org, id))),

  /** Brak uruchomienia kolektora = 404 -> pusty raport (widok pokazuje stan pusty z CTA). */
  /**
   * Czy pliki raportu jeszcze istnieją: po okresie przechowywania API odpowiada 410 `report_expired`
   * (rekord i skróty zostają). Inne błędy nie blokują widoku: ramka sama pokaże, co zwróciło API.
   */
  reportStatus: async (
    org: string,
    runId: string,
    signal?: AbortSignal,
  ): Promise<"available" | "expired"> => {
    try {
      const response = await fetch(reportUrl(org, runId, "html"), {
        credentials: "include",
        cache: "no-store",
        signal,
      });
      // sam status wystarczy: nie pobieramy treści raportu (ramka pobierze ją osobno)
      void response.body?.cancel().catch(() => undefined);
      return response.status === 410 ? "expired" : "available";
    } catch {
      return "available";
    }
  },
  evidence: async (org: string): Promise<EvidenceReport> => {
    try {
      return toEvidence(await request<WireEvidence>(paths.evidence(org)));
    } catch (error) {
      if (isApiError(error) && error.status === 404) {
        return {
          run_id: null,
          collected_at: null,
          host: null,
          collector_command: null,
          controls: [],
        };
      }
      throw error;
    }
  },
  usage: async (org: string): Promise<Usage> => toUsage(await request<WireUsage>(paths.usage(org))),
  getSchedule: (org: string) => request<Schedule>(paths.schedule(org)),
  updateSchedule: (org: string, body: ScheduleSettings) =>
    request<Schedule>(paths.schedule(org), { method: "PUT", json: body }),
  verifyHash: (org: string, sha256: string) =>
    request<VerifyResult>(paths.verify(org), { method: "POST", json: { sha256 } }),

  listWebhooks: async (org: string): Promise<Webhook[]> =>
    itemsOf(await request<WirePage<WireWebhook> | WireWebhook[]>(paths.webhooks(org))).map(
      toWebhook,
    ),
  createWebhook: async (org: string, body: CreateWebhookRequest): Promise<Webhook> =>
    toWebhook(await request<WireWebhook>(paths.webhooks(org), { method: "POST", json: body })),
  deleteWebhook: (org: string, id: string) =>
    request<void>(paths.webhook(org, id), { method: "DELETE" }),
  pingWebhook: async (org: string, id: string): Promise<WebhookPingResult> =>
    toWebhookPing(await request<WireWebhookPing>(paths.webhookPing(org, id), { method: "POST" })),
};

/* ------------------------------------------------------------------ */
/* Adresy do przeglądarki (ramka, pobieranie, SSE)                     */
/* ------------------------------------------------------------------ */

export function reportUrl(org: string, runId: string, fmt: ReportFormat): string {
  return `${apiUrl(paths.runReport(org, runId))}?fmt=${fmt}`;
}

export function jobEventsUrl(org: string, jobId: string): string {
  return apiUrl(paths.jobEvents(org, jobId));
}

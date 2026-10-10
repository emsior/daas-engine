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
  Member,
  Me,
  Org,
  ProblemDetails,
  ReportFormat,
  Run,
  UpdateOrgRequest,
  UploadPreview,
  Usage,
  VerifyResult,
  Webhook,
  WebhookPingResult,
} from "./types";

/* ------------------------------------------------------------------ */
/* Błędy                                                               */
/* ------------------------------------------------------------------ */

export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string | null;
  readonly requestId: string | null;
  readonly code: string | null;
  readonly fieldErrors: { field?: string; message: string }[];

  constructor(init: {
    status: number;
    title: string;
    detail?: string | null;
    requestId?: string | null;
    code?: string | null;
    fieldErrors?: { field?: string; message: string }[];
  }) {
    super(init.detail ? `${init.title}: ${init.detail}` : init.title);
    this.name = "ApiError";
    this.status = init.status;
    this.title = init.title;
    this.detail = init.detail ?? null;
    this.requestId = init.requestId ?? null;
    this.code = init.code ?? null;
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
    fieldErrors: problem?.errors ?? [],
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
  me: (signal?: AbortSignal) => request<Me>(paths.me(), { signal }),
  requestMagicLink: (email: string) =>
    request<void>(paths.magicLink(), { method: "POST", json: { email } }),
  verifyMagicLink: (token: string) =>
    request<Me>(paths.magicLinkVerify(), { method: "POST", json: { token } }),
  logout: () => request<void>(paths.logout(), { method: "POST" }),

  createOrg: (body: CreateOrgRequest) => request<Org>(paths.orgs(), { method: "POST", json: body }),
  getOrg: (org: string) => request<Org>(paths.org(org)),
  updateOrg: (org: string, body: UpdateOrgRequest) =>
    request<Org>(paths.org(org), { method: "PATCH", json: body }),

  listMembers: (org: string) => request<Member[]>(paths.members(org)),
  invite: (org: string, body: InviteRequest) =>
    request<Invitation>(paths.invitations(org), { method: "POST", json: body }),

  listApiKeys: (org: string) => request<ApiKey[]>(paths.apiKeys(org)),
  createApiKey: (org: string, body: CreateApiKeyRequest) =>
    request<ApiKeyCreated>(paths.apiKeys(org), { method: "POST", json: body }),
  revokeApiKey: (org: string, id: string) =>
    request<void>(paths.apiKey(org, id), { method: "DELETE" }),

  upload: (org: string, file: File, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadPreview>(paths.uploads(org), { method: "POST", form, signal });
  },
  createJob: (org: string, body: CreateJobRequest) =>
    request<Job>(paths.jobs(org), { method: "POST", json: body }),
  getJob: (org: string, id: string, signal?: AbortSignal) =>
    request<Job>(paths.job(org, id), { signal }),
  cancelJob: (org: string, id: string) =>
    request<Job>(paths.jobCancel(org, id), { method: "POST" }),

  listRuns: (org: string, limit = 50) => request<Run[]>(paths.runs(org), { query: { limit } }),
  getRun: (org: string, id: string) => request<Run>(paths.run(org, id)),

  evidence: (org: string) => request<EvidenceReport>(paths.evidence(org)),
  usage: (org: string) => request<Usage>(paths.usage(org)),
  verifyHash: (org: string, sha256: string) =>
    request<VerifyResult>(paths.verify(org), { method: "POST", json: { sha256 } }),

  listWebhooks: (org: string) => request<Webhook[]>(paths.webhooks(org)),
  createWebhook: (org: string, body: CreateWebhookRequest) =>
    request<Webhook>(paths.webhooks(org), { method: "POST", json: body }),
  deleteWebhook: (org: string, id: string) =>
    request<void>(paths.webhook(org, id), { method: "DELETE" }),
  pingWebhook: (org: string, id: string) =>
    request<WebhookPingResult>(paths.webhookPing(org, id), { method: "POST" }),
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

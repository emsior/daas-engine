/**
 * Kształty odpowiedzi backendu (`app/api/v1/schemas.py`) i funkcje mapujące je na typy kokpitu (`types.ts`).
 *
 * Widoki korzystają wyłącznie z typów z `types.ts`; rozjazdy między kontraktem backendu a modelem widoków
 * są zamknięte w tym jednym pliku. Po wygenerowaniu `docs/openapi.json` (`npm run gen:api`) interfejsy
 * `Wire*` można zastąpić aliasami do `components["schemas"][...]`.
 */
import type {
  CheckStatus,
  EvidenceControl,
  EvidenceReport,
  Invitation,
  Me,
  Org,
  Run,
  UploadPreview,
  Usage,
  Webhook,
  WebhookPingResult,
} from "./types";
import type { components } from "./schema";

type Schemas = components["schemas"];

export type WirePage<T> = { items: T[]; next_cursor?: string | null };
export type WireMe = Schemas["MeOut"];
export type WireSession = Schemas["SessionOut"];
export type WireRun = Schemas["RunOut"];
export type WireRunDetail = Schemas["RunDetail"];
export type WireUpload = Schemas["UploadOut"];
export type WireEvidence = Schemas["EvidenceOut"];
export type WireUsage = Schemas["UsageReport"];
export type WireWebhook = Schemas["WebhookOut"] | Schemas["WebhookCreated"];
export type WireWebhookPing = Schemas["WebhookPingResult"];
export type WireInvitation = Schemas["InvitationOut"];

/** Polecenie kolektora (stałe, bo API nie zwraca go w statusie kontroli). */
export const COLLECTOR_COMMAND = "powershell -ExecutionPolicy Bypass -File .\\uksc-collector.ps1";

/** Lista (`[...]`) albo strona (`{items, next_cursor}`) - backend zwraca strony dla runów i zadań. */
export function itemsOf<T>(payload: WirePage<T> | T[]): T[] {
  return Array.isArray(payload) ? payload : payload.items;
}

export function toMe(wire: WireMe, org: Org | null): Me {
  if (!wire.user) {
    throw new Error("Odpowiedź /me bez użytkownika (klucz API zamiast sesji)");
  }
  const membership = wire.orgs[0];
  return {
    user: { ...wire.user, name: wire.user.name ?? null },
    org: membership ? org : null,
    role: membership?.role ?? null,
  };
}

function definedHashes(wire: Record<string, string | null>): Run["report_sha256"] {
  const out: Run["report_sha256"] = {};
  for (const format of ["html", "md", "json"] as const) {
    const hash = wire[format];
    if (hash) out[format] = hash;
  }
  return out;
}

/** Metryki z runu: do kart trafiają tylko wartości skalarne. */
function scalarMetrics(metrics: Record<string, unknown> | undefined): Run["metrics"] {
  const out: Run["metrics"] = {};
  for (const [key, value] of Object.entries(metrics ?? {})) {
    if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
      out[key] = value as string | number | boolean | null;
    }
  }
  return out;
}

export function toRun(wire: WireRun | WireRunDetail): Run {
  const detail = "metrics" in wire ? wire : null;
  return {
    run_id: wire.run_id,
    pipeline: wire.pipeline,
    status: wire.status,
    created_at: wire.created_at,
    report_sha256: definedHashes(wire.report_sha256),
    metrics: scalarMetrics(detail?.metrics),
    ...(detail && typeof detail.records_processed === "number"
      ? { records_processed: detail.records_processed }
      : {}),
  };
}

export function toUploadPreview(wire: WireUpload): UploadPreview {
  const isPackage = wire.kind === "package";
  const rows = wire.rows ?? 0;
  return {
    upload_id: wire.id,
    filename: wire.filename,
    kind: wire.kind,
    bytes: wire.bytes,
    rows,
    columns: wire.columns ?? [],
    mapping: wire.mapping ?? {},
    unmapped_columns: wire.unmapped_columns ?? [],
    generated: wire.generated ?? [],
    warnings: wire.warnings ?? [],
    confidence: wire.confidence ?? (isPackage ? 1 : 0),
    // paczka kolektora jest walidowana po stronie serwera już przy zapisie: bez wierszy do mapowania
    ready: wire.ready ?? isPackage,
    suggested_pipeline: wire.suggested_pipeline,
  };
}

function asText(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

export function toEvidence(wire: WireEvidence): EvidenceReport {
  const controls: EvidenceControl[] = wire.checks.map((check) => ({
    control_id: check.control_id,
    title: check.title ?? check.control_id,
    requirement: check.requirement ?? "",
    status: (check.status ?? "ERROR") as CheckStatus,
    artifact: {
      sha256: asText(check.artifact.sha256),
      collected_at: asText(check.artifact.collected_at),
      source: asText(check.artifact.source),
    },
    collector_command: null,
  }));
  return {
    run_id: wire.run_id,
    collected_at: wire.collected_at ?? null,
    host: wire.host ?? null,
    collector_command: COLLECTOR_COMMAND,
    controls,
  };
}

export function toUsage(wire: WireUsage): Usage {
  return {
    period: wire.current.period,
    runs: wire.current.runs,
    upload_bytes: wire.current.upload_bytes,
    runs_limit: wire.limits.runs_per_month,
    upload_bytes_limit: wire.limits.upload_bytes_per_month,
  };
}

export function toWebhook(wire: WireWebhook): Webhook {
  return {
    id: wire.id,
    url: wire.url,
    events: wire.events,
    active: wire.active,
    created_at: wire.created_at,
    ...("secret" in wire && wire.secret ? { secret: wire.secret } : {}),
  };
}

export function toWebhookPing(wire: WireWebhookPing): WebhookPingResult {
  return { ok: wire.delivered, status_code: wire.status ?? null };
}

export function toInvitation(wire: WireInvitation): Invitation {
  return { id: wire.id, email: wire.email, role: wire.role, expires_at: wire.expires_at };
}

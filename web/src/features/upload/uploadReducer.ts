import type { JobStatus, UploadPreview } from "@/lib/api/types";
import type { UploadRejection } from "@/lib/upload-validation";

/** Błąd widoczny przy polu i w podsumowaniu; komunikat składa widok na podstawie kodu. */
export type UploadError =
  | UploadRejection
  | { code: "none" }
  | { code: "server"; requestId?: string | null }
  | { code: "columns"; columns: string[] }
  | { code: "notReady" };

export type UploadPhase =
  "choose" | "uploading" | "preview" | "starting" | "running" | "done" | "failed" | "cancelled";

export type LiveMode = "sse" | "reconnecting" | "polling";

export interface UploadProgress {
  percent: number;
  rowsDone: number | null;
  rowsTotal: number | null;
  status: JobStatus | null;
}

export interface UploadState {
  phase: UploadPhase;
  file: File | null;
  error: UploadError | null;
  /** Licznik błędów: zmiana przenosi fokus na podsumowanie. */
  attempt: number;
  preview: UploadPreview | null;
  startFailed: boolean;
  jobId: string | null;
  progress: UploadProgress;
  live: LiveMode;
  runId: string | null;
  failCode: string | null;
}

export const initialProgress: UploadProgress = {
  percent: 0,
  rowsDone: null,
  rowsTotal: null,
  status: null,
};

export const initialState: UploadState = {
  phase: "choose",
  file: null,
  error: null,
  attempt: 0,
  preview: null,
  startFailed: false,
  jobId: null,
  progress: initialProgress,
  live: "sse",
  runId: null,
  failCode: null,
};

export type UploadAction =
  | { type: "select"; file: File }
  | { type: "reject"; error: UploadError }
  | { type: "upload_start" }
  | { type: "upload_ok"; preview: UploadPreview }
  | { type: "upload_fail"; error: UploadError }
  | { type: "back_to_choose" }
  | { type: "job_start" }
  | { type: "job_created"; jobId: string; status: JobStatus }
  | { type: "job_start_fail" }
  | { type: "status"; status: JobStatus }
  | { type: "progress"; percent: number; rowsDone?: number; rowsTotal?: number }
  | { type: "live"; mode: LiveMode }
  | { type: "finished"; status: JobStatus; runId: string | null; errorCode: string | null }
  | { type: "restart" };

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, Math.round(value)));
}

export function uploadReducer(state: UploadState, action: UploadAction): UploadState {
  switch (action.type) {
    case "select":
      return { ...state, file: action.file, error: null };
    case "reject":
      return { ...state, file: null, error: action.error, attempt: state.attempt + 1 };
    case "upload_start":
      return { ...state, phase: "uploading", error: null };
    case "upload_ok":
      return {
        ...state,
        phase: "preview",
        preview: action.preview,
        error: null,
        startFailed: false,
      };
    case "upload_fail":
      return {
        ...state,
        phase: "choose",
        error: action.error,
        attempt: state.attempt + 1,
        preview: null,
      };
    case "back_to_choose":
      return { ...state, phase: "choose", preview: null, error: null, startFailed: false };
    case "job_start":
      return { ...state, phase: "starting", startFailed: false };
    case "job_created":
      return {
        ...state,
        phase: "running",
        jobId: action.jobId,
        progress: { ...initialProgress, status: action.status },
        live: "sse",
      };
    case "job_start_fail":
      return { ...state, phase: "preview", startFailed: true, attempt: state.attempt + 1 };
    case "status":
      return { ...state, progress: { ...state.progress, status: action.status } };
    case "progress":
      return {
        ...state,
        progress: {
          ...state.progress,
          percent: clampPercent(action.percent),
          rowsDone: action.rowsDone ?? state.progress.rowsDone,
          rowsTotal: action.rowsTotal ?? state.progress.rowsTotal,
        },
      };
    case "live":
      return { ...state, live: action.mode };
    case "finished": {
      if (action.status === "succeeded") {
        // bez run_id czekamy na odpytywanie zadania, które go poda
        if (!action.runId) return { ...state, live: "polling" };
        return {
          ...state,
          phase: "done",
          runId: action.runId,
          progress: { ...state.progress, percent: 100 },
        };
      }
      if (action.status === "cancelled") return { ...state, phase: "cancelled" };
      if (action.status === "failed")
        return { ...state, phase: "failed", failCode: action.errorCode ?? "unknown" };
      return state;
    }
    case "restart":
      return { ...initialState };
  }
}

/** Numer aktywnego etapu (0-3) dla wskaźnika kreatora. */
export function stepOf(phase: UploadPhase): number {
  switch (phase) {
    case "choose":
    case "uploading":
      return 0;
    case "preview":
    case "starting":
      return 1;
    case "running":
      return 2;
    default:
      return 3;
  }
}

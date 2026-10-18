"use client";

import Link from "next/link";
import { useEffect, useReducer, useRef, type FormEvent } from "react";
import { useAnnounce } from "@/components/Announcer";
import { Alert } from "@/components/Alert";
import { Button, LinkButton } from "@/components/Button";
import { Card } from "@/components/Card";
import { Dropzone } from "@/components/Dropzone";
import { ErrorSummary } from "@/components/ErrorSummary";
import { PageHeading } from "@/components/PageHeading";
import { Progress } from "@/components/Progress";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/Table";
import { useSession } from "@/components/shell/OrgGate";
import { useT, useTDynamic, type TDynamicFunction, type TFunction } from "@/i18n/provider";
import { api, isApiError, jobEventsUrl } from "@/lib/api/client";
import { useCreateJob, useJobPolling, useUpload } from "@/lib/api/hooks";
import type { UploadPreview } from "@/lib/api/types";
import { MAX_PACKAGE_UPLOAD_BYTES, MAX_TABLE_UPLOAD_BYTES } from "@/lib/env";
import { formatBytes, formatNumber } from "@/lib/format";
import { connectJobEvents } from "@/lib/sse";
import { defaultPipelineFor, kindOfFile } from "@/lib/upload-validation";
import { classifyUploadFailure } from "./uploadErrors";
import {
  initialState,
  stepOf,
  uploadReducer,
  type UploadError,
  type UploadState,
} from "./uploadReducer";

const FILE_ID = "upload-file";
const STEPS = [
  "upload.step.choose",
  "upload.step.check",
  "upload.step.process",
  "upload.step.done",
] as const;

export function uploadErrorMessage(error: UploadError, t: TFunction): string {
  switch (error.code) {
    case "type":
      return t("upload.error.type");
    case "size":
      return t("upload.error.size", { limit: formatBytes(error.limitBytes) });
    case "empty":
      return t("upload.error.empty");
    case "none":
      return t("upload.error.none");
    case "columns":
      return t("upload.error.columns", { columns: error.columns.join(", ") });
    case "notReady":
      return t("upload.error.notReady");
    case "columnsServer":
      return t("upload.error.columnsServer");
    case "package":
      return t("upload.error.package");
    case "decode":
      return t("upload.error.decode");
    case "records":
      return t("upload.error.records");
    case "xlsx":
      return t("upload.error.xlsx");
    case "quota":
      return t("upload.error.quota");
    case "server":
      return error.requestId
        ? `${t("upload.error.server")} ${t("app.requestId", { id: error.requestId })}`
        : t("upload.error.server");
  }
}

export function progressLabel(progress: UploadState["progress"], t: TFunction): string {
  if (progress.rowsDone !== null && progress.rowsTotal !== null) {
    return t("upload.progress.rows", {
      done: formatNumber(progress.rowsDone),
      total: formatNumber(progress.rowsTotal),
    });
  }
  if (progress.status === "queued") return t("upload.progress.queued");
  return t("upload.progress.percent", { pct: `${formatNumber(progress.percent)}\u00a0%` });
}

function MappingPreview({
  preview,
  isPackage,
  td,
}: {
  preview: UploadPreview;
  isPackage: boolean;
  td: TDynamicFunction;
}) {
  const t = useT();
  const entries = Object.entries(preview.mapping);
  return (
    <div className="stack-4">
      <ul className="stack-1 list-none p-0">
        <li>{t("upload.preview.file", { name: preview.filename })}</li>
        {!isPackage ? (
          <li className="tabular">
            {t("upload.preview.rows", { rows: formatNumber(preview.rows) })}
          </li>
        ) : null}
        {!isPackage ? (
          <li className="tabular">
            {t("upload.preview.confidence", { pct: `${Math.round(preview.confidence * 100)}%` })}
          </li>
        ) : null}
      </ul>
      {isPackage || entries.length === 0 ? (
        <p>{t("upload.preview.package")}</p>
      ) : (
        <Table caption={t("upload.preview.caption")}>
          <Thead>
            <Tr>
              <Th>{t("upload.preview.col.field")}</Th>
              <Th>{t("upload.preview.col.column")}</Th>
            </Tr>
          </Thead>
          <Tbody>
            {entries.map(([canonical, original]) => (
              <Tr key={canonical}>
                <Td label={t("upload.preview.col.field")}>{td(`field.${canonical}`, canonical)}</Td>
                <Td label={t("upload.preview.col.column")}>
                  <code>{original}</code>
                </Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      )}
      {preview.unmapped_columns.length > 0 ? (
        <p className="text-muted">
          {t("upload.preview.unmapped", { columns: preview.unmapped_columns.join(", ") })}
        </p>
      ) : null}
      {preview.generated.length > 0 ? (
        <p className="text-muted">
          {t("upload.preview.generated", {
            fields: preview.generated.map((f) => td(`field.${f}`, f)).join(", "),
          })}
        </p>
      ) : null}
      {preview.warnings.length > 0 ? (
        <Alert variant="warning" role="status" title={t("upload.preview.warnings")}>
          <ul className="warning-list">
            {preview.warnings.map((warning, index) => (
              <li key={`${index}-${warning}`}>{warning}</li>
            ))}
          </ul>
        </Alert>
      ) : null}
    </div>
  );
}

export function UploadWizard() {
  const t = useT();
  const td = useTDynamic();
  const announce = useAnnounce();
  const { orgId } = useSession();
  const [state, dispatch] = useReducer(uploadReducer, initialState);
  const upload = useUpload(orgId);
  const createJob = useCreateJob(orgId);
  const poll = useJobPolling(
    orgId,
    state.jobId,
    state.phase === "running" && state.live === "polling",
  );

  const focusRef = useRef<HTMLElement | null>(null);
  const previousPhase = useRef(state.phase);

  /* --- SSE: postęp zadania na żywo, z ponawianiem --- */
  const { phase, jobId } = state;
  useEffect(() => {
    if (phase !== "running" || !jobId) return;
    return connectJobEvents(jobEventsUrl(orgId, jobId), {
      onOpen: () => dispatch({ type: "live", mode: "sse" }),
      onStatus: (event) => {
        dispatch({ type: "status", status: event.status });
        if (event.progress !== undefined) dispatch({ type: "progress", percent: event.progress });
      },
      onProgress: (event) =>
        dispatch({
          type: "progress",
          percent: event.progress,
          rowsDone: event.rows_done,
          rowsTotal: event.rows_total,
        }),
      onDone: (event) =>
        dispatch({
          type: "finished",
          status: event.status,
          runId: event.run_id ?? null,
          errorCode: event.error_code ?? null,
        }),
      onReconnecting: () => dispatch({ type: "live", mode: "reconnecting" }),
      onGiveUp: () => dispatch({ type: "live", mode: "polling" }),
    });
  }, [phase, jobId, orgId]);

  /* --- odpytywanie jako zapasowa ścieżka --- */
  const polled = poll.data;
  useEffect(() => {
    if (!polled || phase !== "running") return;
    if (
      polled.status === "succeeded" ||
      polled.status === "failed" ||
      polled.status === "cancelled"
    ) {
      dispatch({
        type: "finished",
        status: polled.status,
        runId: polled.run_id,
        errorCode: polled.error_code,
      });
    } else {
      dispatch({ type: "status", status: polled.status });
      dispatch({ type: "progress", percent: polled.progress });
    }
  }, [polled, phase]);

  /* --- fokus i ogłoszenia przy zmianie etapu --- */
  useEffect(() => {
    const before = previousPhase.current;
    previousPhase.current = phase;
    if (before === phase) return;
    if (phase === "preview" && before === "uploading") announce(t("upload.uploaded"));
    if (phase === "done") announce(t("upload.announce.done"));
    if (phase === "choose" && before === "preview") document.getElementById(FILE_ID)?.focus();
    else if (
      phase === "preview" ||
      phase === "running" ||
      phase === "done" ||
      phase === "failed" ||
      phase === "cancelled"
    ) {
      focusRef.current?.focus();
    }
  }, [phase, announce, t]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const file = state.file;
    if (!file) {
      dispatch({ type: "reject", error: { code: "none" } });
      return;
    }
    dispatch({ type: "upload_start" });
    upload.mutate(file, {
      onSuccess: (preview) => {
        if (preview.missing_columns && preview.missing_columns.length > 0) {
          dispatch({
            type: "upload_fail",
            error: { code: "columns", columns: preview.missing_columns },
          });
        } else if (!preview.ready) {
          dispatch({ type: "upload_fail", error: { code: "notReady" } });
        } else {
          dispatch({ type: "upload_ok", preview });
        }
      },
      onError: (error) =>
        dispatch({
          type: "upload_fail",
          error: classifyUploadFailure(error, kindOfFile(file.name)),
        }),
    });
  };

  const startJob = () => {
    if (!state.preview) return;
    const kind = kindOfFile(state.file?.name ?? state.preview.filename);
    const pipeline = state.preview.suggested_pipeline ?? defaultPipelineFor(kind ?? "csv");
    dispatch({ type: "job_start" });
    const autoJobId = state.preview.auto_job_id;
    if (autoJobId) {
      // harmonogram: API zakolejkowało zadanie zaraz po uploadzie - śledzimy je zamiast tworzyć drugie
      dispatch({ type: "job_created", jobId: autoJobId, status: "queued" });
      return;
    }
    createJob.mutate(
      { pipeline, upload_id: state.preview.upload_id },
      {
        onSuccess: (job) => dispatch({ type: "job_created", jobId: job.id, status: job.status }),
        onError: (error) => {
          if (isApiError(error) && error.code === "upload_required") {
            dispatch({ type: "back_to_choose" });
            dispatch({ type: "reject", error: classifyUploadFailure(error, null) });
          } else {
            dispatch({ type: "job_start_fail" });
          }
        },
      },
    );
  };

  const cancelJob = () => {
    if (state.jobId) void api.cancelJob(orgId, state.jobId).catch(() => undefined);
  };

  const errorMessage = state.error ? uploadErrorMessage(state.error, t) : null;
  const step = stepOf(state.phase);
  const isPackage = state.file
    ? kindOfFile(state.file.name) === "package"
    : state.preview?.kind === "package";

  return (
    <div className="view">
      <PageHeading lead={t("upload.lead")}>{t("upload.title")}</PageHeading>

      <ol className="steps" aria-label={t("upload.steps.label")}>
        {STEPS.map((key, index) => (
          <li
            key={key}
            data-done={index < step ? "true" : undefined}
            aria-current={index === step ? "step" : undefined}
          >
            {t(key)}
          </li>
        ))}
      </ol>

      {errorMessage ? (
        <ErrorSummary
          title={t("form.summaryTitle")}
          errors={[{ id: FILE_ID, message: errorMessage }]}
          focusKey={state.attempt}
        />
      ) : null}

      {(state.phase === "choose" || state.phase === "uploading") && (
        <form
          className="stack-6"
          onSubmit={onSubmit}
          noValidate
          aria-busy={state.phase === "uploading"}
        >
          <Dropzone
            id={FILE_ID}
            title={t("upload.dropzone.title")}
            orText={t("upload.dropzone.or")}
            buttonLabel={state.file ? t("upload.dropzone.change") : t("upload.dropzone.button")}
            hint={t("upload.dropzone.hint", {
              limit: formatBytes(MAX_TABLE_UPLOAD_BYTES),
              packageLimit: formatBytes(MAX_PACKAGE_UPLOAD_BYTES),
            })}
            selectedText={
              state.file
                ? t("upload.dropzone.selected", {
                    name: state.file.name,
                    size: formatBytes(state.file.size),
                  })
                : null
            }
            error={errorMessage}
            disabled={state.phase === "uploading"}
            onFile={(file) => dispatch({ type: "select", file })}
            onReject={(rejection) => dispatch({ type: "reject", error: rejection })}
          />
          <div className="cluster">
            <Button type="submit" cta loading={state.phase === "uploading"}>
              {state.phase === "uploading" ? t("upload.uploading") : t("upload.submit")}
            </Button>
            <a className="btn btn-ghost" href="/samples/client_export_pl.csv" download>
              {t("upload.sample")}
            </a>
          </div>
          {state.phase === "uploading" ? (
            <p role="status" className="text-muted">
              {t("upload.uploading.status")}
            </p>
          ) : null}
        </form>
      )}

      {(state.phase === "preview" || state.phase === "starting") && state.preview ? (
        <Card
          title={t("upload.preview.title")}
          headingRef={(node) => {
            focusRef.current = node;
          }}
        >
          <div className="stack-6">
            <MappingPreview preview={state.preview} isPackage={Boolean(isPackage)} td={td} />
            {state.startFailed ? (
              <Alert variant="danger" role="alert">
                <p>{t("upload.error.start")}</p>
              </Alert>
            ) : null}
            <div className="cluster">
              <Button cta loading={state.phase === "starting"} onClick={startJob}>
                {state.phase === "starting" ? t("upload.starting") : t("upload.start")}
              </Button>
              <Button variant="secondary" onClick={() => dispatch({ type: "back_to_choose" })}>
                {t("upload.dropzone.change")}
              </Button>
            </div>
          </div>
        </Card>
      ) : null}

      {state.phase === "running" ? (
        <Card
          title={t("upload.progress.title")}
          headingRef={(node) => {
            focusRef.current = node;
          }}
        >
          <div className="stack-4">
            <Progress label={progressLabel(state.progress, t)} value={state.progress.percent} />
            {state.live === "reconnecting" ? (
              <p className="text-muted">{t("upload.progress.reconnecting")}</p>
            ) : null}
            {state.live === "polling" ? (
              <p className="text-muted">{t("upload.progress.polling")}</p>
            ) : null}
            {state.progress.status === "running" ? (
              <p className="text-muted">{t("upload.progress.noCancel")}</p>
            ) : (
              <div>
                <Button variant="secondary" onClick={cancelJob}>
                  {t("upload.progress.cancel")}
                </Button>
              </div>
            )}
          </div>
        </Card>
      ) : null}

      {state.phase === "done" && state.runId ? (
        <Alert
          variant="success"
          role="status"
          tabIndex={-1}
          ref={(node) => {
            focusRef.current = node;
          }}
        >
          <div className="stack-3">
            <strong className="alert-title">{t("upload.done.title")}</strong>
            <p>{t("upload.done.body")}</p>
            <div className="cluster">
              <LinkButton href={`/runs/${state.runId}`}>{t("upload.done.open")}</LinkButton>
              <Button variant="secondary" onClick={() => dispatch({ type: "restart" })}>
                {t("upload.done.another")}
              </Button>
            </div>
          </div>
        </Alert>
      ) : null}

      {state.phase === "failed" || state.phase === "cancelled" ? (
        <Alert
          variant="danger"
          tabIndex={-1}
          ref={(node) => {
            focusRef.current = node;
          }}
        >
          <div className="stack-3">
            <strong className="alert-title">
              {state.phase === "failed" ? t("upload.failed.title") : t("upload.cancelled.title")}
            </strong>
            {state.phase === "failed" ? (
              <p>
                {state.failCode === "no_records"
                  ? t("upload.failed.noRecords")
                  : t("upload.failed.body", { code: state.failCode ?? "unknown" })}
              </p>
            ) : null}
            <div className="cluster">
              <Button onClick={() => dispatch({ type: "back_to_choose" })}>{t("app.retry")}</Button>
              <Link href="/">{t("notFound.cta")}</Link>
            </div>
          </div>
        </Alert>
      ) : null}
    </div>
  );
}

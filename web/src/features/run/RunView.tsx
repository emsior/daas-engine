"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Alert } from "@/components/Alert";
import { Badge } from "@/components/Badge";
import { Card } from "@/components/Card";
import { PageHeading } from "@/components/PageHeading";
import { QueryState } from "@/components/QueryState";
import { Skeleton } from "@/components/Skeleton";
import { useSession } from "@/components/shell/OrgGate";
import { useT, useTDynamic } from "@/i18n/provider";
import { reportUrl } from "@/lib/api/client";
import { useReportStatus, useRun } from "@/lib/api/hooks";
import type { ReportFormat, Run } from "@/lib/api/types";
import { formatDate, formatDateTime } from "@/lib/format";
import { runStatusToBadge } from "@/lib/status";
import { VerifyBlock } from "./VerifyBlock";

const DOWNLOADS: {
  format: ReportFormat;
  label: "run.download.html" | "run.download.md" | "run.download.json";
}[] = [
  { format: "html", label: "run.download.html" },
  { format: "md", label: "run.download.md" },
  { format: "json", label: "run.download.json" },
];

function RunDetails({ run, orgId }: { run: Run; orgId: string }) {
  const t = useT();
  const td = useTDynamic();
  const typeName = td(`pipeline.${run.pipeline}`, run.pipeline);
  const date = formatDate(run.created_at);
  const htmlUrl = reportUrl(orgId, run.run_id, "html");
  const status = useReportStatus(orgId, run.run_id);
  const expired = status.data === "expired";

  return (
    <>
      <Card>
        <dl className="meta-list">
          <dt>{t("run.meta.type")}</dt>
          <dd>{typeName}</dd>
          <dt>{t("run.meta.status")}</dt>
          <dd>
            <Badge
              status={runStatusToBadge(run.status)}
              label={td(`run.status.${run.status}`, t("badge.unknown"))}
            />
          </dd>
          <dt>{t("run.meta.created")}</dt>
          <dd className="tabular">{formatDateTime(run.created_at)}</dd>
        </dl>
      </Card>

      {expired ? (
        <Alert variant="warning" role="status">
          <p>{t("error.report_expired")}</p>
        </Alert>
      ) : (
        <section className="stack-3" aria-labelledby="report-actions">
          <h2 id="report-actions" className="sr-only">
            {t("run.download.title")}
          </h2>
          <div className="cluster">
            <a className="btn" href={htmlUrl} target="_blank" rel="noopener noreferrer">
              {t("run.open")}
            </a>
            {DOWNLOADS.map(({ format, label }) => (
              <a
                key={format}
                className="btn btn-secondary"
                href={reportUrl(orgId, run.run_id, format)}
                download
              >
                {t(label)}
              </a>
            ))}
          </div>
          {/* sandbox="" bez allow-scripts i allow-same-origin: raport nie wykona skryptów */}
          <iframe
            className="iframe-report"
            sandbox=""
            title={t("run.iframe.title", { type: typeName, date })}
            referrerPolicy="no-referrer"
            loading="lazy"
            src={htmlUrl}
          />
        </section>
      )}

      <VerifyBlock hashes={run.report_sha256} />
    </>
  );
}

export function RunView() {
  const t = useT();
  const params = useParams<{ id: string }>();
  const id = params.id;
  const { orgId } = useSession();
  const run = useRun(orgId, id);

  return (
    <div className="view">
      <PageHeading>
        {run.data ? t("run.title", { date: formatDate(run.data.created_at) }) : t("run.heading")}
      </PageHeading>
      <QueryState
        query={run}
        loadingLabel={t("run.loading")}
        errorTitle={t("run.error.title")}
        skeleton={
          <>
            <Skeleton variant="kpi" />
            <Skeleton lines={5} />
          </>
        }
      >
        {(data) => <RunDetails run={data} orgId={orgId} />}
      </QueryState>
      <p>
        <Link href="/">{t("run.back")}</Link>
      </p>
    </div>
  );
}

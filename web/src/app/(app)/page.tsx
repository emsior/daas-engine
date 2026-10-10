"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Badge } from "@/components/Badge";
import { LinkButton } from "@/components/Button";
import { Card } from "@/components/Card";
import { Kpi } from "@/components/Kpi";
import { PageHeading } from "@/components/PageHeading";
import { QueryState } from "@/components/QueryState";
import { Skeleton } from "@/components/Skeleton";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/Table";
import { useSession } from "@/components/shell/OrgGate";
import { useT, useTDynamic } from "@/i18n/provider";
import { useRun, useRuns } from "@/lib/api/hooks";
import type { Run } from "@/lib/api/types";
import { formatDate, formatDateTime } from "@/lib/format";
import { runKpis } from "@/lib/run-kpis";
import { runStatusToBadge } from "@/lib/status";

type SortDirection = "asc" | "desc";

function useRunLabels() {
  const t = useT();
  const td = useTDynamic();
  return {
    pipeline: (run: Run) => td(`pipeline.${run.pipeline}`, run.pipeline),
    status: (run: Run) => td(`run.status.${run.status}`, t("badge.unknown")),
    range: (run: Run) =>
      run.period_from || run.period_to
        ? `${formatDate(run.period_from)} – ${formatDate(run.period_to)}`
        : t("dashboard.archive.noRange"),
  };
}

function EmptyDashboard() {
  const t = useT();
  return (
    <Card title={t("dashboard.empty.title")}>
      <div className="stack-4">
        <p>{t("dashboard.empty.body")}</p>
        <div className="cluster">
          <LinkButton href="/upload" cta>
            {t("dashboard.empty.pick")}
          </LinkButton>
          <a className="btn btn-secondary" href="/samples/client_export_pl.csv" download>
            {t("dashboard.empty.sample")}
          </a>
        </div>
      </div>
    </Card>
  );
}

function LatestRun({ run, orgId }: { run: Run; orgId: string }) {
  const t = useT();
  const labels = useRunLabels();
  // lista uruchomień nie niesie metryk: liczby do karty pochodzą ze szczegółów ostatniego runu
  const detail = useRun(orgId, run.run_id);
  const kpis = runKpis(detail.data ?? run);
  return (
    <Card title={t("dashboard.latest.title")}>
      <div className="stack-4">
        <div className="cluster">
          <span className="tabular">
            {t("dashboard.latest.date", { date: formatDate(run.created_at) })}
          </span>
          <Badge status={runStatusToBadge(run.status)} label={labels.status(run)} />
        </div>
        <p className="text-muted">{labels.pipeline(run)}</p>
        <div className="kpi-row">
          {kpis.map((kpi) => (
            <Kpi
              key={kpi.labelKey}
              label={t(kpi.labelKey)}
              value={<span className="tabular nowrap">{kpi.value}</span>}
            />
          ))}
        </div>
        <div>
          <LinkButton href={`/runs/${run.run_id}`} variant="secondary">
            {t("dashboard.latest.open")}
          </LinkButton>
        </div>
      </div>
    </Card>
  );
}

function UploadCta() {
  const t = useT();
  return (
    <Card title={t("dashboard.cta.title")}>
      <div className="stack-4">
        <p>{t("dashboard.cta.body")}</p>
        <div>
          <LinkButton href="/upload" cta>
            {t("dashboard.cta")}
          </LinkButton>
        </div>
      </div>
    </Card>
  );
}

function Archive({ runs }: { runs: Run[] }) {
  const t = useT();
  const labels = useRunLabels();
  const [direction, setDirection] = useState<SortDirection>("desc");

  const sorted = useMemo(() => {
    const copy = [...runs];
    copy.sort((a, b) => {
      const diff = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      return direction === "asc" ? diff : -diff;
    });
    return copy;
  }, [runs, direction]);

  return (
    <section className="stack-3" aria-labelledby="archive-title">
      <h2 id="archive-title">{t("dashboard.archive.title")}</h2>
      <Table caption={t("dashboard.archive.caption")}>
        <Thead>
          <Tr>
            <Th aria-sort={direction === "asc" ? "ascending" : "descending"}>
              <button
                type="button"
                className="sort-button"
                data-direction={direction}
                onClick={() => setDirection((d) => (d === "desc" ? "asc" : "desc"))}
              >
                {t("dashboard.archive.col.date")}
              </button>
            </Th>
            <Th>{t("dashboard.archive.col.range")}</Th>
            <Th>{t("dashboard.archive.col.type")}</Th>
            <Th>{t("dashboard.archive.col.status")}</Th>
            <Th>{t("dashboard.archive.col.action")}</Th>
          </Tr>
        </Thead>
        <Tbody>
          {sorted.map((run) => (
            <Tr key={run.run_id}>
              <Td label={t("dashboard.archive.col.date")} className="tabular nowrap">
                {formatDateTime(run.created_at)}
              </Td>
              <Td label={t("dashboard.archive.col.range")} className="tabular">
                {labels.range(run)}
              </Td>
              <Td label={t("dashboard.archive.col.type")}>{labels.pipeline(run)}</Td>
              <Td label={t("dashboard.archive.col.status")}>
                <Badge status={runStatusToBadge(run.status)} label={labels.status(run)} />
              </Td>
              <Td label={t("dashboard.archive.col.action")}>
                <Link href={`/runs/${run.run_id}`} className="nowrap">
                  {t("dashboard.archive.open")}
                  <span className="sr-only">
                    {" "}
                    {t("dashboard.archive.open.sr", { date: formatDate(run.created_at) })}
                  </span>
                </Link>
              </Td>
            </Tr>
          ))}
        </Tbody>
      </Table>
    </section>
  );
}

export default function DashboardPage() {
  const t = useT();
  const { orgId } = useSession();
  const runs = useRuns(orgId);

  return (
    <div className="view">
      <PageHeading>{t("dashboard.title")}</PageHeading>
      <QueryState
        query={runs}
        loadingLabel={t("dashboard.loading")}
        errorTitle={t("dashboard.error.title")}
        skeleton={
          <>
            <Skeleton variant="kpi" />
            <Skeleton lines={4} />
          </>
        }
        isEmpty={(data) => data.length === 0}
        empty={<EmptyDashboard />}
      >
        {(data) => {
          const latest = [...data].sort(
            (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
          )[0];
          return (
            <>
              <div className="view-columns">
                <div className="lg:order-2">
                  <UploadCta />
                </div>
                {latest ? (
                  <div className="lg:order-1">
                    <LatestRun run={latest} orgId={orgId} />
                  </div>
                ) : null}
              </div>
              <Archive runs={data} />
            </>
          );
        }}
      </QueryState>
    </div>
  );
}

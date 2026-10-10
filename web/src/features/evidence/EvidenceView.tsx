"use client";

import { useState } from "react";
import { Badge } from "@/components/Badge";
import { Button, LinkButton } from "@/components/Button";
import { Card } from "@/components/Card";
import { CopyButton } from "@/components/CopyButton";
import { HashDisplay } from "@/components/HashDisplay";
import { PageHeading } from "@/components/PageHeading";
import { QueryState } from "@/components/QueryState";
import { Skeleton } from "@/components/Skeleton";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/Table";
import { useSession } from "@/components/shell/OrgGate";
import { useT, useTDynamic } from "@/i18n/provider";
import { useEvidence } from "@/lib/api/hooks";
import type { EvidenceControl, EvidenceReport } from "@/lib/api/types";
import { formatDate, formatDateTime } from "@/lib/format";
import { shortHash } from "@/lib/hash";
import { checkStatusToBadge } from "@/lib/status";

function ControlRows({
  control,
  fallbackCommand,
}: {
  control: EvidenceControl;
  fallbackCommand: string | null;
}) {
  const t = useT();
  const td = useTDynamic();
  const [open, setOpen] = useState(false);
  const detailsId = `evidence-details-${control.control_id}`;
  const command = control.collector_command ?? fallbackCommand;
  const artifact = control.artifact;

  return (
    <>
      <Tr>
        <Td label={t("evidence.col.control")}>
          <strong>{control.control_id}</strong>
          <br />
          <span className="text-muted">{control.title}</span>
        </Td>
        <Td label={t("evidence.col.requirement")}>{control.requirement}</Td>
        <Td label={t("evidence.col.status")}>
          <Badge
            status={checkStatusToBadge(control.status)}
            label={td(`check.${control.status}`, control.status)}
          />
        </Td>
        <Td label={t("evidence.col.artifact")}>
          {artifact?.sha256 ? (
            <code className="hash nowrap" title={artifact.sha256}>
              {shortHash(artifact.sha256)}
            </code>
          ) : (
            <span className="text-muted">{t("evidence.noArtifact")}</span>
          )}
        </Td>
        <Td label={t("evidence.col.date")} className="tabular nowrap">
          {formatDate(artifact?.collected_at)}
        </Td>
        <Td label={t("evidence.col.details")}>
          <Button
            variant="secondary"
            aria-expanded={open}
            aria-controls={detailsId}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? t("evidence.collapse") : t("evidence.expand")}
            <span className="sr-only"> {t("evidence.expand.sr", { id: control.control_id })}</span>
          </Button>
        </Td>
      </Tr>
      {open ? (
        <Tr id={detailsId} className="row-details">
          <Td label={t("evidence.col.details")} colSpan={6}>
            <div className="stack-3">
              <h3>{t("evidence.details.command")}</h3>
              {command ? (
                <>
                  <code className="code">{command}</code>
                  <div>
                    <CopyButton
                      value={command}
                      accessibleName={`${t("copy.button")}: ${t("evidence.details.command")} ${control.control_id}`}
                    />
                  </div>
                </>
              ) : (
                <p className="text-muted">{t("evidence.details.noCommand")}</p>
              )}
              {artifact?.sha256 ? (
                <div className="hash-block">
                  <span className="text-muted">{t("evidence.details.hash")}</span>
                  <HashDisplay hash={artifact.sha256} />
                </div>
              ) : null}
              {artifact?.source ? (
                <p className="text-muted">
                  {t("evidence.details.source", { source: artifact.source })}
                </p>
              ) : null}
            </div>
          </Td>
        </Tr>
      ) : null}
    </>
  );
}

function EvidenceTable({ report }: { report: EvidenceReport }) {
  const t = useT();
  return (
    <div className="stack-4">
      <p className="text-muted">
        {report.collected_at ? (
          <span className="tabular">
            {t("evidence.last", { date: formatDateTime(report.collected_at) })}
          </span>
        ) : null}
        {report.host ? <span> {t("evidence.host", { host: report.host })}</span> : null}
      </p>
      <Table caption={t("evidence.caption")}>
        <Thead>
          <Tr>
            <Th>{t("evidence.col.control")}</Th>
            <Th>{t("evidence.col.requirement")}</Th>
            <Th>{t("evidence.col.status")}</Th>
            <Th>{t("evidence.col.artifact")}</Th>
            <Th>{t("evidence.col.date")}</Th>
            <Th>{t("evidence.col.details")}</Th>
          </Tr>
        </Thead>
        <Tbody>
          {report.controls.map((control) => (
            <ControlRows
              key={control.control_id}
              control={control}
              fallbackCommand={report.collector_command}
            />
          ))}
        </Tbody>
      </Table>
    </div>
  );
}

export function EvidenceView() {
  const t = useT();
  const { orgId } = useSession();
  const evidence = useEvidence(orgId);

  return (
    <div className="view">
      <PageHeading lead={t("evidence.lead")}>{t("evidence.title")}</PageHeading>
      <QueryState
        query={evidence}
        loadingLabel={t("evidence.loading")}
        errorTitle={t("evidence.error.title")}
        skeleton={<Skeleton lines={6} />}
        isEmpty={(data) => data.controls.length === 0}
        empty={
          <Card title={t("evidence.empty.title")}>
            <div className="stack-4">
              <p>{t("evidence.empty.body")}</p>
              <div>
                <LinkButton href="/upload" cta>
                  {t("evidence.empty.cta")}
                </LinkButton>
              </div>
            </div>
          </Card>
        }
      >
        {(report) => <EvidenceTable report={report} />}
      </QueryState>
    </div>
  );
}

"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { Badge, type BadgeStatus } from "@/components/Badge";
import { LinkButton } from "@/components/Button";
import { Card } from "@/components/Card";
import { Kpi } from "@/components/Kpi";
import { PageHeading } from "@/components/PageHeading";
import { QueryState } from "@/components/QueryState";
import { Skeleton } from "@/components/Skeleton";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/Table";
import { useSession } from "@/components/shell/OrgGate";
import type { MessageKey } from "@/i18n";
import { useT, useTDynamic } from "@/i18n/provider";
import { useDataHealth } from "@/lib/api/hooks";
import type {
  DataHealth,
  HealthIssue,
  HealthIssueCode,
  HealthSeverity,
  WeekHealth,
} from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { formatDateTimeInZone, formatNumber } from "@/lib/format";
import {
  barPercent,
  barWidthClass,
  formatRunSeconds,
  healthVerdict,
  internalHref,
  isEmptyHealth,
  isoWeekNumber,
  scaleMax,
  weekRange,
  type Verdict,
} from "./weekBars";

/** Zakresy tabeli tygodni (przyciski-radio). Werdykt i wskaźniki nie zależą od zakresu (API liczy je z 26 tyg.). */
export const WEEK_RANGES = [8, 12, 26] as const;
export type WeekRange = (typeof WEEK_RANGES)[number];
const DEFAULT_RANGE: WeekRange = 12;

/** Kompletna mapa kodów z API (nowy kod bez tekstu = błąd kompilacji). */
const ISSUE_TEXT: Record<HealthIssueCode, MessageKey> = {
  last_run_failed: "health.issue.last_run_failed",
  plan_limit_reached: "health.issue.plan_limit_reached",
  stale_upload: "health.issue.stale_upload",
  plan_limit_near: "health.issue.plan_limit_near",
  partial_reports: "health.issue.partial_reports",
  no_uploads_yet: "health.issue.no_uploads_yet",
  reminder_off: "health.issue.reminder_off",
  auto_run_off: "health.issue.auto_run_off",
};

const SEVERITY: Record<HealthSeverity, { badge: BadgeStatus; label: MessageKey }> = {
  error: { badge: "error", label: "health.severity.error" },
  warning: { badge: "warn", label: "health.severity.warning" },
  info: { badge: "pending", label: "health.severity.info" },
};

const VERDICT_WORD: Record<Verdict, MessageKey> = {
  safe: "health.verdict.safe",
  at_risk: "health.verdict.at_risk",
  no_data: "health.verdict.no_data",
};

function verdictBody(verdict: Verdict, tone: BadgeStatus): MessageKey {
  if (verdict === "no_data") return "health.verdict.no_data.body";
  if (verdict === "safe") return "health.verdict.safe.body";
  return tone === "error" ? "health.verdict.error.body" : "health.verdict.warn.body";
}

/** Data i godzina w strefie harmonogramu (tygodnie też są liczone w tej strefie). */
function inZone(value: string, timeZone: string): { date: string; time: string } {
  const [date = "—", time = ""] = formatDateTimeInZone(value, timeZone).split(" ");
  return { date, time };
}

/* ------------------------------------------------------------------ */
/* Sekcja 1: werdykt i lista problemów                                 */
/* ------------------------------------------------------------------ */

function IssueList({ data }: { data: DataHealth }) {
  const t = useT();
  const titleId = useId();
  if (data.issues.length === 0) return <p className="text-muted">{t("health.issues.none")}</p>;
  return (
    <section className="stack-3" aria-labelledby={titleId}>
      <h3 id={titleId}>{t("health.issues.title")}</h3>
      <ul className="stack-3 list-none p-0" role="list">
        {data.issues.map((issue: HealthIssue) => (
          <li key={issue.code} className="cluster-2">
            <Badge
              status={SEVERITY[issue.severity].badge}
              label={t(SEVERITY[issue.severity].label)}
            />
            <Link href={internalHref(issue.link)}>{t(ISSUE_TEXT[issue.code])}</Link>
            {issue.code === "last_run_failed" && data.summary.last_error_code ? (
              <span className="text-muted">
                {t("health.issue.code", { code: data.summary.last_error_code })}
              </span>
            ) : null}
            {issue.since ? (
              <span className="text-muted tabular nowrap">
                {t("health.issue.since", { date: inZone(issue.since, data.timezone).date })}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function VerdictCard({ data }: { data: DataHealth }) {
  const t = useT();
  const { verdict, tone } = healthVerdict(data);
  return (
    <Card
      title={
        <span className="cluster-2">
          <span>{t("health.verdict.title")}</span>{" "}
          <Badge status={tone} label={t(VERDICT_WORD[verdict])} />
        </span>
      }
    >
      <div className="stack-4">
        <p>{t(verdictBody(verdict, tone))}</p>
        {verdict === "no_data" ? (
          <div>
            <LinkButton href="/upload" cta>
              {t("health.verdict.cta")}
            </LinkButton>
          </div>
        ) : null}
        <IssueList data={data} />
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Sekcja 2: wskaźniki                                                 */
/* ------------------------------------------------------------------ */

function Kpis({ data }: { data: DataHealth }) {
  const t = useT();
  const td = useTDynamic();
  const titleId = useId();
  const s = data.summary;
  const plan = td(`health.plan.${s.plan}`, s.plan);
  const day = (value: string | null) => (value ? inZone(value, data.timezone).date : t("app.none"));
  const reminder =
    s.reminder_enabled && s.next_reminder_at ? inZone(s.next_reminder_at, data.timezone) : null;
  return (
    <section aria-labelledby={titleId}>
      <h2 id={titleId} className="sr-only">
        {t("health.kpi.title")}
      </h2>
      <div className="kpi-row">
        <Kpi
          label={t("health.kpi.streak")}
          value={<span className="tabular">{formatNumber(s.streak_weeks)}</span>}
          hint={t("health.kpi.streak.hint")}
        />
        <Kpi
          label={t("health.kpi.lastUpload")}
          value={<span className="tabular nowrap">{day(s.last_upload_at)}</span>}
        />
        <Kpi
          label={t("health.kpi.lastSuccess")}
          value={<span className="tabular nowrap">{day(s.last_success_at)}</span>}
        />
        <Kpi
          label={t("health.kpi.usage")}
          value={
            <span className="tabular nowrap">
              {s.runs_limit === null
                ? formatNumber(s.runs_used)
                : t("health.kpi.usage.value", {
                    used: formatNumber(s.runs_used),
                    limit: formatNumber(s.runs_limit),
                  })}
            </span>
          }
          hint={
            s.runs_limit === null
              ? t("health.kpi.usage.unlimited", { plan })
              : t("health.kpi.usage.hint", { plan })
          }
        />
        <Kpi
          label={t("health.kpi.reminder")}
          value={
            <span className="tabular nowrap">
              {reminder ? reminder.date : t("health.kpi.reminder.off")}
            </span>
          }
          hint={
            reminder
              ? t("health.kpi.reminder.hint", { time: reminder.time, tz: data.timezone })
              : undefined
          }
        />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Sekcja 3: tygodnie                                                  */
/* ------------------------------------------------------------------ */

function RangeToggle({
  value,
  onChange,
}: {
  value: WeekRange;
  onChange: (value: WeekRange) => void;
}) {
  const t = useT();
  const name = useId();
  return (
    <fieldset className="fieldset">
      <legend>{t("health.weeks.range")}</legend>
      <div className="cluster-4">
        {WEEK_RANGES.map((weeks) => {
          const id = `${name}-${weeks}`;
          return (
            <div key={weeks} className="check">
              <input
                id={id}
                type="radio"
                name={name}
                value={weeks}
                className="check__input"
                checked={value === weeks}
                onChange={() => onChange(weeks)}
              />
              <div className="check__body">
                <label htmlFor={id} className="check__label">
                  {t("health.weeks.option", { n: weeks })}
                </label>
              </div>
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}

function WeeksTable({ weeks }: { weeks: WeekHealth[] }) {
  const t = useT();
  const max = scaleMax(weeks.map((week) => week.reports));
  const rows = [...weeks].reverse(); // od najnowszego, jak archiwum raportów
  const col = {
    week: t("health.weeks.col.week"),
    uploads: t("health.weeks.col.uploads"),
    reports: t("health.weeks.col.reports"),
    failed: t("health.weeks.col.failed"),
    avg: t("health.weeks.col.avg"),
  };
  return (
    <Table caption={t("health.weeks.caption")}>
      <Thead>
        <Tr>
          <Th>{col.week}</Th>
          <Th className="num">{col.uploads}</Th>
          <Th>{col.reports}</Th>
          <Th className="num">{col.failed}</Th>
          <Th className="num">{col.avg}</Th>
        </Tr>
      </Thead>
      <Tbody>
        {rows.map((week) => {
          const percent = barPercent(week.reports, max);
          const number = isoWeekNumber(week.week_start);
          return (
            <Tr key={week.week_start}>
              <Td label={col.week}>
                <span className="stack-1">
                  <span className="tabular nowrap">{weekRange(week.week_start)}</span>
                  <span className="cluster-2 text-muted">
                    {number !== null ? <span>{t("health.weeks.number", { n: number })}</span> : null}
                    {week.partial ? (
                      <Badge status="pending" label={t("health.weeks.current")} />
                    ) : null}
                  </span>
                </span>
              </Td>
              <Td label={col.uploads} numeric>
                {formatNumber(week.uploads)}
              </Td>
              <Td label={col.reports}>
                <span className="flex items-center gap-2">
                  <span className="tabular min-w-6 text-end">{formatNumber(week.reports)}</span>
                  <span
                    aria-hidden="true"
                    data-testid="health-bar"
                    className="block h-2 min-w-16 flex-1 rounded-full bg-surface-sunken"
                  >
                    <span
                      data-percent={percent}
                      className={cx("block h-2 rounded-full bg-primary", barWidthClass(percent))}
                    />
                  </span>
                </span>
              </Td>
              <Td label={col.failed} numeric>
                {formatNumber(week.runs_failed)}
              </Td>
              <Td label={col.avg} numeric>
                {formatRunSeconds(week.avg_run_seconds)}
              </Td>
            </Tr>
          );
        })}
      </Tbody>
    </Table>
  );
}

function WeeksSection({
  data,
  range,
  onRange,
  updating,
}: {
  data: DataHealth;
  range: WeekRange;
  onRange: (value: WeekRange) => void;
  updating: boolean;
}) {
  const t = useT();
  const titleId = useId();
  return (
    <section className="stack-4" aria-labelledby={titleId}>
      <div className="cluster cluster-between">
        <h2 id={titleId}>{t("health.weeks.title", { n: data.weeks.length })}</h2>
        <RangeToggle value={range} onChange={onRange} />
      </div>
      <p className="text-muted">{t("health.weeks.zone", { tz: data.timezone })}</p>
      <p role="status" className="sr-only">
        {updating ? t("health.weeks.updating") : ""}
      </p>
      <div aria-busy={updating || undefined}>
        <WeeksTable weeks={data.weeks} />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Widok                                                               */
/* ------------------------------------------------------------------ */

/** Pulpit „Zdrowie danych” organizacji: werdykt, wskaźniki i tabela tygodni z przełącznikiem 8/12/26. */
export function DataHealthView({ orgId }: { orgId: string }) {
  const t = useT();
  const [range, setRange] = useState<WeekRange>(DEFAULT_RANGE);
  const query = useDataHealth(orgId, range);
  return (
    <div className="view">
      <PageHeading lead={t("health.lead")}>{t("health.title")}</PageHeading>
      <QueryState
        query={query}
        loadingLabel={t("health.loading")}
        errorTitle={t("health.error.title")}
        skeleton={
          <>
            <Skeleton variant="kpi" />
            <Skeleton lines={4} />
          </>
        }
      >
        {(data) =>
          isEmptyHealth(data) ? (
            <VerdictCard data={data} />
          ) : (
            <>
              <VerdictCard data={data} />
              <Kpis data={data} />
              <WeeksSection
                data={data}
                range={range}
                onRange={setRange}
                updating={query.isPlaceholderData}
              />
            </>
          )
        }
      </QueryState>
    </div>
  );
}

/** Ekran w ramie aplikacji: organizacja z sesji (`OrgGate`). */
export function DataHealthScreen() {
  const { orgId } = useSession();
  return <DataHealthView orgId={orgId} />;
}

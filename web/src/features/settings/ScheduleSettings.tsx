"use client";

import { useState, type FormEvent } from "react";
import { Alert } from "@/components/Alert";
import { useAnnounce } from "@/components/Announcer";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Checkbox } from "@/components/Checkbox";
import { ErrorSummary } from "@/components/ErrorSummary";
import { Field } from "@/components/Field";
import { Input, Select } from "@/components/Input";
import { QueryState } from "@/components/QueryState";
import { Skeleton } from "@/components/Skeleton";
import { useT } from "@/i18n/provider";
import { isApiError } from "@/lib/api/client";
import { useSchedule, useUpdateSchedule } from "@/lib/api/hooks";
import type { Schedule, ScheduleSettings } from "@/lib/api/types";
import { describeError } from "@/lib/errors";
import { formatDateTimeInZone } from "@/lib/format";
import { toSummary } from "@/lib/validation";

const IDS = {
  reminder: "schedule-reminder",
  weekday: "schedule-weekday",
  hour: "schedule-hour",
  timezone: "schedule-timezone",
  skip: "schedule-skip",
  autorun: "schedule-autorun",
  notify: "schedule-notify",
} as const;
const TZ_LIST_ID = "schedule-timezone-list";
const ERROR_ORDER = [IDS.weekday, IDS.hour, IDS.timezone];
const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6] as const;
const HOURS = Array.from({ length: 24 }, (_, hour) => hour);
/** Podpowiedzi strefy (pole przyjmuje każdą nazwę IANA; walidacja jest po stronie API). */
export const TIMEZONE_SUGGESTIONS = [
  "Europe/Warsaw",
  "UTC",
  "Europe/London",
  "Europe/Berlin",
  "Europe/Prague",
  "Europe/Vilnius",
  "Europe/Kyiv",
  "America/New_York",
] as const;

/** Pole odpowiedzi 422 (`errors[].loc`) -> id kontrolki formularza. */
const FIELD_IDS: Record<string, string> = {
  reminder_weekday: IDS.weekday,
  reminder_hour: IDS.hour,
  timezone: IDS.timezone,
};

function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

function settingsOf(schedule: Schedule): ScheduleSettings {
  return {
    reminder_enabled: schedule.reminder_enabled,
    reminder_weekday: schedule.reminder_weekday,
    reminder_hour: schedule.reminder_hour,
    timezone: schedule.timezone,
    reminder_skip_if_recent_upload: schedule.reminder_skip_if_recent_upload,
    auto_run_on_upload: schedule.auto_run_on_upload,
    notify_on_finish: schedule.notify_on_finish,
  };
}

function NextReminder({ schedule }: { schedule: Schedule }) {
  const t = useT();
  return (
    <p className="text-muted tabular" data-testid="schedule-next">
      {schedule.reminder_enabled && schedule.next_reminder_at
        ? t("schedule.next", {
            date: formatDateTimeInZone(schedule.next_reminder_at, schedule.timezone),
            tz: schedule.timezone,
          })
        : t("schedule.next.off")}
    </p>
  );
}

function ScheduleReadOnly({ schedule }: { schedule: Schedule }) {
  const t = useT();
  const onOff = (value: boolean) => (value ? t("schedule.on") : t("schedule.off"));
  const rows: [string, string][] = [
    [
      t("schedule.reminder.label"),
      schedule.reminder_enabled
        ? `${onOff(true)}: ${t("schedule.when", {
            weekday: t(`schedule.weekday.${schedule.reminder_weekday}` as "schedule.weekday.0"),
            hour: hourLabel(schedule.reminder_hour),
            tz: schedule.timezone,
          })}`
        : onOff(false),
    ],
    [t("schedule.skip.label"), onOff(schedule.reminder_skip_if_recent_upload)],
    [t("schedule.autorun.label"), onOff(schedule.auto_run_on_upload)],
    [t("schedule.notify.label"), onOff(schedule.notify_on_finish)],
  ];
  return (
    <div className="stack-4">
      <p>{t("schedule.readonly")}</p>
      <dl className="stack-3">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted">{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <NextReminder schedule={schedule} />
    </div>
  );
}

function ScheduleForm({ orgId, schedule }: { orgId: string; schedule: Schedule }) {
  const t = useT();
  const announce = useAnnounce();
  const update = useUpdateSchedule(orgId);
  const [values, setValues] = useState<ScheduleSettings>(() => settingsOf(schedule));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [saved, setSaved] = useState(false);

  const set = <K extends keyof ScheduleSettings>(key: K, value: ScheduleSettings[K]) => {
    setSaved(false);
    setValues((current) => ({ ...current, [key]: value }));
  };

  const fieldMessage = (id: string): string =>
    id === IDS.weekday
      ? t("schedule.weekday.error")
      : id === IDS.hour
        ? t("schedule.hour.error")
        : t("schedule.timezone.error");

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setSaved(false);
    setFormError(null);
    const timezone = values.timezone.trim();
    if (!timezone) {
      setErrors({ [IDS.timezone]: t("schedule.timezone.error") });
      setAttempt((n) => n + 1);
      return;
    }
    setErrors({});
    update.mutate(
      { ...values, timezone },
      {
        onSuccess: (result) => {
          setValues(settingsOf(result));
          setSaved(true);
          announce(t("schedule.saved"));
        },
        onError: (error) => {
          const next: Record<string, string> = {};
          if (isApiError(error) && error.status === 422) {
            for (const entry of error.fieldErrors) {
              const id = entry.field ? FIELD_IDS[entry.field] : undefined;
              if (id) next[id] = fieldMessage(id);
            }
          }
          if (Object.keys(next).length > 0) {
            setErrors(next);
            setAttempt((n) => n + 1);
          } else {
            setFormError(describeError(error, t).message);
          }
        },
      },
    );
  };

  const summary = toSummary(ERROR_ORDER, errors);

  return (
    <form className="stack-4" onSubmit={onSubmit} noValidate aria-label={t("schedule.title")}>
      {summary.length > 0 ? (
        <ErrorSummary title={t("form.summaryTitle")} errors={summary} focusKey={attempt} />
      ) : null}
      {formError ? (
        <Alert variant="danger" role="alert" title={t("error.title")}>
          {formError}
        </Alert>
      ) : null}

      <Checkbox
        id={IDS.reminder}
        role="switch"
        name="reminder_enabled"
        checked={values.reminder_enabled}
        onChange={(event) => set("reminder_enabled", event.target.checked)}
        label={t("schedule.reminder.label")}
        hint={t("schedule.reminder.hint")}
      />
      <div className="cluster">
        <Field id={IDS.weekday} label={t("schedule.weekday.label")} error={errors[IDS.weekday]}>
          {(control) => (
            <Select
              {...control}
              name="reminder_weekday"
              value={values.reminder_weekday}
              onChange={(event) => set("reminder_weekday", Number(event.target.value))}
            >
              {WEEKDAYS.map((day) => (
                <option key={day} value={day}>
                  {t(`schedule.weekday.${day}`)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field id={IDS.hour} label={t("schedule.hour.label")} error={errors[IDS.hour]}>
          {(control) => (
            <Select
              {...control}
              name="reminder_hour"
              value={values.reminder_hour}
              onChange={(event) => set("reminder_hour", Number(event.target.value))}
            >
              {HOURS.map((hour) => (
                <option key={hour} value={hour}>
                  {hourLabel(hour)}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      <Field
        id={IDS.timezone}
        label={t("schedule.timezone.label")}
        hint={t("schedule.timezone.hint")}
        error={errors[IDS.timezone]}
        required
      >
        {(control) => (
          <Input
            {...control}
            name="timezone"
            list={TZ_LIST_ID}
            autoComplete="off"
            spellCheck={false}
            className="sm:max-w-80"
            value={values.timezone}
            onChange={(event) => set("timezone", event.target.value)}
          />
        )}
      </Field>
      <datalist id={TZ_LIST_ID}>
        {TIMEZONE_SUGGESTIONS.map((zone) => (
          <option key={zone} value={zone} />
        ))}
      </datalist>
      <Checkbox
        id={IDS.skip}
        role="switch"
        name="reminder_skip_if_recent_upload"
        checked={values.reminder_skip_if_recent_upload}
        onChange={(event) => set("reminder_skip_if_recent_upload", event.target.checked)}
        label={t("schedule.skip.label")}
      />
      <Checkbox
        id={IDS.autorun}
        role="switch"
        name="auto_run_on_upload"
        checked={values.auto_run_on_upload}
        onChange={(event) => set("auto_run_on_upload", event.target.checked)}
        label={t("schedule.autorun.label")}
        hint={t("schedule.autorun.hint")}
      />
      <Checkbox
        id={IDS.notify}
        role="switch"
        name="notify_on_finish"
        checked={values.notify_on_finish}
        onChange={(event) => set("notify_on_finish", event.target.checked)}
        label={t("schedule.notify.label")}
        hint={t("schedule.notify.hint")}
      />
      <NextReminder schedule={update.data ?? schedule} />
      <div className="cluster">
        <Button type="submit" loading={update.isPending}>
          {t("schedule.save")}
        </Button>
        {saved ? (
          <span role="status" className="text-muted">
            {t("schedule.saved")}
          </span>
        ) : null}
      </div>
    </form>
  );
}

/** Sekcja „Harmonogram i powiadomienia" w Ustawieniach. Edycja: role z `org:manage` (owner, admin). */
export function ScheduleSettingsCard({ orgId, canManage }: { orgId: string; canManage: boolean }) {
  const t = useT();
  const schedule = useSchedule(orgId);
  return (
    <Card title={t("schedule.title")}>
      <div className="stack-4">
        <p>{t("schedule.lead")}</p>
        <QueryState
          query={schedule}
          loadingLabel={t("schedule.loading")}
          errorTitle={t("schedule.error")}
          skeleton={<Skeleton lines={4} />}
        >
          {(data) =>
            canManage ? (
              <ScheduleForm orgId={orgId} schedule={data} />
            ) : (
              <ScheduleReadOnly schedule={data} />
            )
          }
        </QueryState>
      </div>
    </Card>
  );
}

"use client";

import { useState, type FormEvent } from "react";
import { useAnnounce } from "@/components/Announcer";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Checkbox } from "@/components/Checkbox";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CopyButton } from "@/components/CopyButton";
import { Dialog } from "@/components/Dialog";
import { ErrorSummary } from "@/components/ErrorSummary";
import { Field } from "@/components/Field";
import { Input } from "@/components/Input";
import { Kpi } from "@/components/Kpi";
import { PageHeading } from "@/components/PageHeading";
import { QueryState } from "@/components/QueryState";
import { Skeleton } from "@/components/Skeleton";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/Table";
import { useSession } from "@/components/shell/OrgGate";
import { useT } from "@/i18n/provider";
import {
  useCreateWebhook,
  useDeleteWebhook,
  usePingWebhook,
  useUpdateOrg,
  useUsage,
  useWebhooks,
} from "@/lib/api/hooks";
import type { Org, Webhook } from "@/lib/api/types";
import { describeError } from "@/lib/errors";
import { formatBytes, formatNumber } from "@/lib/format";
import { isHttpsUrl, parseRetentionDays, toSummary } from "@/lib/validation";
import { SettingsNav } from "./SettingsNav";

const RETENTION_ID = "settings-retention";
const LLM_ID = "settings-llm";
const WEBHOOK_URL_ID = "webhook-url";

function RetentionAndLlm({ org }: { org: Org }) {
  const t = useT();
  const announce = useAnnounce();
  const update = useUpdateOrg(org.id);
  const [retention, setRetention] = useState(String(org.retention_days));
  const [llm, setLlm] = useState(org.llm_narrative);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);
  const [saved, setSaved] = useState(false);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setSaved(false);
    const days = parseRetentionDays(retention);
    if (days === null) {
      setErrors({ [RETENTION_ID]: t("settings.retention.error") });
      setAttempt((n) => n + 1);
      return;
    }
    setErrors({});
    update.mutate(
      { retention_days: days, llm_narrative: llm },
      {
        onSuccess: () => {
          setSaved(true);
          announce(t("settings.saved"));
        },
        onError: (error) => {
          setErrors({ [RETENTION_ID]: describeError(error, t).message });
          setAttempt((n) => n + 1);
        },
      },
    );
  };

  const summary = toSummary([RETENTION_ID], errors);

  return (
    <form className="stack-6" onSubmit={onSubmit} noValidate>
      {summary.length > 0 ? (
        <ErrorSummary title={t("form.summaryTitle")} errors={summary} focusKey={attempt} />
      ) : null}
      <Card title={t("settings.retention.title")}>
        <Field
          id={RETENTION_ID}
          label={t("settings.retention.label")}
          hint={t("settings.retention.hint")}
          error={errors[RETENTION_ID]}
          required
        >
          {(control) => (
            <Input
              {...control}
              name="retention_days"
              inputMode="numeric"
              autoComplete="off"
              className="sm:max-w-48"
              value={retention}
              onChange={(event) => setRetention(event.target.value)}
            />
          )}
        </Field>
      </Card>
      <Card title={t("settings.llm.title")}>
        <Checkbox
          id={LLM_ID}
          role="switch"
          name="llm_narrative"
          checked={llm}
          onChange={(event) => setLlm(event.target.checked)}
          label={t("settings.llm.label")}
          hint={t("settings.llm.desc")}
        />
      </Card>
      <div className="cluster">
        <Button type="submit" loading={update.isPending}>
          {t("settings.save")}
        </Button>
        {saved ? (
          <span role="status" className="text-muted">
            {t("settings.saved")}
          </span>
        ) : null}
      </div>
    </form>
  );
}

function UsageCard({ orgId }: { orgId: string }) {
  const t = useT();
  const usage = useUsage(orgId);
  return (
    <Card title={t("settings.usage.title")}>
      <QueryState
        query={usage}
        loadingLabel={t("app.loading")}
        errorTitle={t("settings.usage.error")}
        skeleton={<Skeleton variant="kpi" lines={2} />}
      >
        {(data) => (
          <div className="stack-4">
            <p className="text-muted tabular">
              {t("settings.usage.period", { period: data.period })}
            </p>
            <div className="kpi-row">
              <Kpi
                label={t("settings.usage.runs")}
                value={<span className="tabular">{formatNumber(data.runs)}</span>}
                hint={
                  data.runs_limit
                    ? t("settings.usage.limit", { limit: formatNumber(data.runs_limit) })
                    : undefined
                }
              />
              <Kpi
                label={t("settings.usage.bytes")}
                value={<span className="tabular nowrap">{formatBytes(data.upload_bytes)}</span>}
                hint={
                  data.upload_bytes_limit
                    ? t("settings.usage.limit", { limit: formatBytes(data.upload_bytes_limit) })
                    : undefined
                }
              />
            </div>
          </div>
        )}
      </QueryState>
    </Card>
  );
}

function WebhooksCard({ orgId }: { orgId: string }) {
  const t = useT();
  const announce = useAnnounce();
  const hooks = useWebhooks(orgId);
  const create = useCreateWebhook(orgId);
  const remove = useDeleteWebhook(orgId);
  const ping = usePingWebhook(orgId);

  const [url, setUrl] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);
  const [secret, setSecret] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<Webhook | null>(null);
  const [pingMessage, setPingMessage] = useState<string | null>(null);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!isHttpsUrl(url)) {
      setErrors({ [WEBHOOK_URL_ID]: t("settings.webhooks.error.url") });
      setAttempt((n) => n + 1);
      return;
    }
    setErrors({});
    create.mutate(
      { url: url.trim(), events: ["run.finished"] },
      {
        onSuccess: (hook) => {
          setUrl("");
          if (hook.secret) setSecret(hook.secret);
        },
        onError: (error) => {
          setErrors({ [WEBHOOK_URL_ID]: describeError(error, t).message });
          setAttempt((n) => n + 1);
        },
      },
    );
  };

  const onPing = (hook: Webhook) => {
    setPingMessage(null);
    ping.mutate(hook.id, {
      onSuccess: (result) => {
        const message = result.ok
          ? t("settings.webhooks.pingOk", { class: result.status_class })
          : result.status_class === "error"
            ? t("settings.webhooks.pingNoAnswer")
            : t("settings.webhooks.pingFailClass", { class: result.status_class });
        setPingMessage(message);
        announce(message);
      },
      onError: () => {
        setPingMessage(t("settings.webhooks.pingFail"));
        announce(t("settings.webhooks.pingFail"));
      },
    });
  };

  const closeSecret = () => {
    setSecret(null);
    create.reset();
  };

  const summary = toSummary([WEBHOOK_URL_ID], errors);

  return (
    <Card title={t("settings.webhooks.title")}>
      <div className="stack-6">
        <p>{t("settings.webhooks.lead")}</p>

        <QueryState
          query={hooks}
          loadingLabel={t("settings.webhooks.loading")}
          errorTitle={t("settings.webhooks.error")}
          skeleton={<Skeleton lines={3} />}
          isEmpty={(data) => data.length === 0}
          empty={<p className="text-muted">{t("settings.webhooks.empty")}</p>}
        >
          {(data) => (
            <Table caption={t("settings.webhooks.caption")}>
              <Thead>
                <Tr>
                  <Th>{t("settings.webhooks.col.url")}</Th>
                  <Th>{t("settings.webhooks.col.events")}</Th>
                  <Th>{t("settings.webhooks.col.status")}</Th>
                  <Th>{t("settings.webhooks.col.actions")}</Th>
                </Tr>
              </Thead>
              <Tbody>
                {data.map((hook) => (
                  <Tr key={hook.id}>
                    <Td label={t("settings.webhooks.col.url")} className="break-all">
                      {hook.url}
                    </Td>
                    <Td label={t("settings.webhooks.col.events")}>{hook.events.join(", ")}</Td>
                    <Td label={t("settings.webhooks.col.status")}>
                      <Badge
                        status={hook.active ? "ok" : "unknown"}
                        label={
                          hook.active
                            ? t("settings.webhooks.active")
                            : t("settings.webhooks.inactive")
                        }
                      />
                    </Td>
                    <Td label={t("settings.webhooks.col.actions")}>
                      <div className="cell-actions">
                        <Button
                          variant="secondary"
                          loading={ping.isPending && ping.variables === hook.id}
                          onClick={() => onPing(hook)}
                        >
                          {t("settings.webhooks.ping")}
                          <span className="sr-only">
                            {" "}
                            {t("settings.webhooks.ping.sr", { url: hook.url })}
                          </span>
                        </Button>
                        <Button variant="ghost" onClick={() => setToDelete(hook)}>
                          {t("settings.webhooks.delete")}
                          <span className="sr-only">
                            {" "}
                            {t("settings.webhooks.delete.sr", { url: hook.url })}
                          </span>
                        </Button>
                      </div>
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          )}
        </QueryState>

        {pingMessage ? (
          <p role="status" className="text-muted">
            {pingMessage}
          </p>
        ) : null}

        <form className="stack-4" onSubmit={onSubmit} noValidate>
          {summary.length > 0 ? (
            <ErrorSummary title={t("form.summaryTitle")} errors={summary} focusKey={attempt} />
          ) : null}
          <Field
            id={WEBHOOK_URL_ID}
            label={t("settings.webhooks.url")}
            hint={t("settings.webhooks.url.hint")}
            error={errors[WEBHOOK_URL_ID]}
            required
          >
            {(control) => (
              <Input
                {...control}
                type="url"
                name="url"
                inputMode="url"
                autoComplete="off"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
              />
            )}
          </Field>
          <div>
            <Button type="submit" loading={create.isPending}>
              {t("settings.webhooks.add")}
            </Button>
          </div>
        </form>
      </div>

      <ConfirmDialog
        open={toDelete !== null}
        title={t("settings.webhooks.delete.title", { url: toDelete?.url ?? "" })}
        description={t("settings.webhooks.delete.body")}
        confirmLabel={t("settings.webhooks.delete")}
        pending={remove.isPending}
        error={remove.isError ? t("settings.webhooks.delete.error") : null}
        onCancel={() => {
          setToDelete(null);
          remove.reset();
        }}
        onConfirm={() => {
          if (toDelete) {
            remove.mutate(toDelete.id, { onSuccess: () => setToDelete(null) });
          }
        }}
      />

      <Dialog
        open={secret !== null}
        onClose={closeSecret}
        title={t("settings.webhooks.secret.title")}
        dismissOnBackdrop={false}
        footer={<Button onClick={closeSecret}>{t("settings.webhooks.secret.done")}</Button>}
      >
        <p>{t("settings.webhooks.secret.body")}</p>
        <code className="code">{secret}</code>
        {secret ? <CopyButton value={secret} label={t("settings.webhooks.secret.copy")} /> : null}
      </Dialog>
    </Card>
  );
}

export function GeneralSettings() {
  const t = useT();
  const { org } = useSession();

  return (
    <div className="view">
      <PageHeading>{t("settings.title")}</PageHeading>
      <SettingsNav />
      <RetentionAndLlm org={org} />
      <UsageCard orgId={org.id} />
      <WebhooksCard orgId={org.id} />
    </div>
  );
}

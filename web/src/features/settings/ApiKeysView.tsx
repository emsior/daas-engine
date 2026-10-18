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
import { Input, Select } from "@/components/Input";
import { PageHeading } from "@/components/PageHeading";
import { QueryState } from "@/components/QueryState";
import { Skeleton } from "@/components/Skeleton";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/Table";
import { useSession } from "@/components/shell/OrgGate";
import { useT } from "@/i18n/provider";
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from "@/lib/api/hooks";
import { API_KEY_SCOPES, type ApiKey } from "@/lib/api/types";
import { describeError } from "@/lib/errors";
import { formatDate, formatDateTime } from "@/lib/format";
import { toSummary } from "@/lib/validation";
import { SettingsNav } from "./SettingsNav";

const NAME_ID = "key-name";
const SCOPES_ID = "key-scopes";
const EXPIRY_ID = "key-expiry";

const EXPIRY_OPTIONS = [
  { value: "30", label: "keys.expiry.30" },
  { value: "90", label: "keys.expiry.90" },
  { value: "365", label: "keys.expiry.365" },
  { value: "never", label: "keys.expiry.never" },
] as const;

export function keyState(key: ApiKey, now: Date = new Date()): "active" | "revoked" | "expired" {
  if (key.revoked_at) return "revoked";
  if (key.expires_at && new Date(key.expires_at).getTime() <= now.getTime()) return "expired";
  return "active";
}

function CreateKeyForm({ orgId }: { orgId: string }) {
  const t = useT();
  const create = useCreateApiKey(orgId);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["reports:read"]);
  const [expiry, setExpiry] = useState<string>("90");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);
  const [revealed, setRevealed] = useState<string | null>(null);

  const toggleScope = (scope: string, checked: boolean) =>
    setScopes((current) =>
      checked ? [...current, scope] : current.filter((item) => item !== scope),
    );

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const next: Record<string, string> = {};
    if (!name.trim()) next[NAME_ID] = t("keys.error.name");
    if (scopes.length === 0) next[`${SCOPES_ID}-${API_KEY_SCOPES[0]}`] = t("keys.error.scopes");
    setErrors(next);
    if (Object.keys(next).length > 0) {
      setAttempt((n) => n + 1);
      return;
    }
    create.mutate(
      { name: name.trim(), scopes, expires_in_days: expiry === "never" ? null : Number(expiry) },
      {
        onSuccess: (created) => {
          setRevealed(created.key);
          setName("");
        },
        onError: (error) => {
          const { message, requestId } = describeError(error, t);
          setErrors({
            [NAME_ID]: `${t("keys.error.create")} ${message}${requestId ? ` (${t("app.requestId", { id: requestId })})` : ""}`,
          });
          setAttempt((n) => n + 1);
        },
      },
    );
  };

  const closeReveal = () => {
    setRevealed(null);
    create.reset();
  };

  const scopeErrorId = `${SCOPES_ID}-${API_KEY_SCOPES[0]}`;
  const summary = toSummary([NAME_ID, scopeErrorId], errors);

  return (
    <Card title={t("keys.create.title")}>
      <form className="stack-6" onSubmit={onSubmit} noValidate>
        {summary.length > 0 ? (
          <ErrorSummary title={t("form.summaryTitle")} errors={summary} focusKey={attempt} />
        ) : null}
        <Field
          id={NAME_ID}
          label={t("keys.name")}
          hint={t("keys.name.hint")}
          error={errors[NAME_ID]}
          required
        >
          {(control) => (
            <Input
              {...control}
              name="name"
              autoComplete="off"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          )}
        </Field>
        <fieldset className="fieldset" aria-describedby={`${SCOPES_ID}-hint`}>
          <legend>{t("keys.scopes")}</legend>
          <div className="hint" id={`${SCOPES_ID}-hint`}>
            {t("keys.scopes.hint")}
          </div>
          {API_KEY_SCOPES.map((scope, index) => (
            <Checkbox
              key={scope}
              id={`${SCOPES_ID}-${scope}`}
              name="scopes"
              value={scope}
              checked={scopes.includes(scope)}
              onChange={(event) => toggleScope(scope, event.target.checked)}
              invalid={index === 0 && Boolean(errors[scopeErrorId])}
              aria-describedby={
                index === 0 && errors[scopeErrorId] ? `${scopeErrorId}-error` : undefined
              }
              label={t(`keys.scope.${scope}`)}
            />
          ))}
          {errors[scopeErrorId] ? (
            <p className="field-error" id={`${scopeErrorId}-error`}>
              <span>{errors[scopeErrorId]}</span>
            </p>
          ) : null}
        </fieldset>
        <Field id={EXPIRY_ID} label={t("keys.expiry")}>
          {(control) => (
            <Select
              {...control}
              name="expiry"
              value={expiry}
              onChange={(event) => setExpiry(event.target.value)}
            >
              {EXPIRY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {t(option.label)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <div>
          <Button type="submit" loading={create.isPending}>
            {t("keys.submit")}
          </Button>
        </div>
      </form>

      <Dialog
        open={revealed !== null}
        onClose={closeReveal}
        title={t("keys.reveal.title")}
        dismissOnBackdrop={false}
        footer={<Button onClick={closeReveal}>{t("keys.reveal.done")}</Button>}
      >
        <p>{t("keys.reveal.body")}</p>
        <div className="stack-2">
          <span className="text-muted">{t("keys.reveal.label")}</span>
          <code className="code" data-testid="revealed-key">
            {revealed}
          </code>
        </div>
        {revealed ? <CopyButton value={revealed} label={t("keys.reveal.copy")} /> : null}
      </Dialog>
    </Card>
  );
}

function KeysTable({ orgId, keys }: { orgId: string; keys: ApiKey[] }) {
  const t = useT();
  const announce = useAnnounce();
  const revoke = useRevokeApiKey(orgId);
  const [target, setTarget] = useState<ApiKey | null>(null);

  return (
    <>
      <Table caption={t("keys.list.caption")}>
        <Thead>
          <Tr>
            <Th>{t("keys.col.name")}</Th>
            <Th>{t("keys.col.prefix")}</Th>
            <Th>{t("keys.col.scopes")}</Th>
            <Th>{t("keys.col.expires")}</Th>
            <Th>{t("keys.col.used")}</Th>
            <Th>{t("keys.col.status")}</Th>
            <Th>{t("keys.col.actions")}</Th>
          </Tr>
        </Thead>
        <Tbody>
          {keys.map((key) => {
            const state = keyState(key);
            return (
              <Tr key={key.id}>
                <Td label={t("keys.col.name")}>
                  <strong>{key.name}</strong>
                </Td>
                <Td label={t("keys.col.prefix")}>
                  <code>{key.prefix}</code>
                </Td>
                <Td label={t("keys.col.scopes")}>
                  <ul className="scope-list">
                    {key.scopes.map((scope) => (
                      <li key={scope}>
                        <code>{scope}</code>
                      </li>
                    ))}
                  </ul>
                </Td>
                <Td label={t("keys.col.expires")} className="tabular nowrap">
                  {key.expires_at ? formatDate(key.expires_at) : t("keys.never")}
                </Td>
                <Td label={t("keys.col.used")} className="tabular nowrap">
                  {key.last_used_at ? formatDateTime(key.last_used_at) : t("keys.neverUsed")}
                </Td>
                <Td label={t("keys.col.status")}>
                  <Badge
                    status={state === "active" ? "ok" : state === "expired" ? "warn" : "error"}
                    label={t(`keys.status.${state}`)}
                  />
                </Td>
                <Td label={t("keys.col.actions")}>
                  {state === "active" ? (
                    <Button variant="secondary" onClick={() => setTarget(key)}>
                      {t("keys.revoke")}
                      <span className="sr-only"> {t("keys.revoke.sr", { name: key.name })}</span>
                    </Button>
                  ) : null}
                </Td>
              </Tr>
            );
          })}
        </Tbody>
      </Table>

      <ConfirmDialog
        open={target !== null}
        title={t("keys.revoke.title", { name: target?.name ?? "" })}
        description={t("keys.revoke.body")}
        confirmLabel={t("keys.revoke.confirm")}
        pending={revoke.isPending}
        error={revoke.isError ? t("keys.revoke.error") : null}
        onCancel={() => {
          setTarget(null);
          revoke.reset();
        }}
        onConfirm={() => {
          if (!target) return;
          revoke.mutate(target.id, {
            onSuccess: () => {
              setTarget(null);
              announce(t("keys.revoked"));
            },
          });
        }}
      />
    </>
  );
}

export function ApiKeysView() {
  const t = useT();
  const { orgId } = useSession();
  const keys = useApiKeys(orgId);

  return (
    <div className="view">
      <PageHeading lead={t("keys.lead")}>{t("keys.title")}</PageHeading>
      <SettingsNav />
      <CreateKeyForm orgId={orgId} />
      <section className="stack-3" aria-labelledby="keys-list-title">
        <h2 id="keys-list-title">{t("keys.list.title")}</h2>
        <QueryState
          query={keys}
          loadingLabel={t("keys.loading")}
          errorTitle={t("keys.error.title")}
          skeleton={<Skeleton lines={4} />}
          isEmpty={(data) => data.length === 0}
          empty={<p className="text-muted">{t("keys.empty")}</p>}
        >
          {(data) => <KeysTable orgId={orgId} keys={data} />}
        </QueryState>
      </section>
    </div>
  );
}

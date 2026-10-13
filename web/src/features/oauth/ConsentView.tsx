"use client";

import { useState } from "react";
import { Alert } from "@/components/Alert";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Checkbox } from "@/components/Checkbox";
import { Field } from "@/components/Field";
import { Select } from "@/components/Input";
import { PageHeading } from "@/components/PageHeading";
import { Skeleton } from "@/components/Skeleton";
import { useSession } from "@/components/shell/OrgGate";
import { useT, useTDynamic } from "@/i18n/provider";
import { isApiError } from "@/lib/api/client";
import { useDecideOAuthRequest, useOAuthRequest } from "@/lib/api/hooks";
import type { OAuthOrgOption, OAuthRequest } from "@/lib/api/types";
import { describeError } from "@/lib/errors";
import { formatDateTime } from "@/lib/format";

const ORG_ID = "oauth-org";
const SCOPES_ID = "oauth-scopes";

/** Przeglądarka wraca do aplikacji tylko pod adres http(s) (zarejestrowany przez API); inny schemat = błąd. */
export function isSafeRedirect(url: string): boolean {
  try {
    const { protocol } = new URL(url);
    return protocol === "https:" || protocol === "http:";
  } catch {
    return false;
  }
}

function navigateAway(url: string) {
  window.location.assign(url);
}

function Gone({ expired }: { expired: boolean }) {
  const t = useT();
  return (
    <div className="view">
      <PageHeading>{t("oauth.consent.title")}</PageHeading>
      <Alert
        variant="warning"
        role="alert"
        title={expired ? t("oauth.consent.expired.title") : t("oauth.consent.notFound.title")}
      >
        <p>{expired ? t("oauth.consent.expired.body") : t("oauth.consent.notFound.body")}</p>
      </Alert>
    </div>
  );
}

interface DecisionFormProps {
  requestId: string;
  data: OAuthRequest;
  defaultOrgId: string | null;
  navigate: (url: string) => void;
}

function initialOrg(data: OAuthRequest, defaultOrgId: string | null): OAuthOrgOption | null {
  return (
    data.organizations.find((org) => org.org_id === defaultOrgId) ?? data.organizations[0] ?? null
  );
}

function DecisionForm({ requestId, data, defaultOrgId, navigate }: DecisionFormProps) {
  const t = useT();
  const td = useTDynamic();
  const decide = useDecideOAuthRequest(requestId);
  const [org, setOrg] = useState<OAuthOrgOption | null>(() => initialOrg(data, defaultOrgId));
  const [chosen, setChosen] = useState<string[]>(() => org?.scopes_grantable ?? []);
  const [error, setError] = useState<string | null>(null);
  const [gone, setGone] = useState<"expired" | "notFound" | null>(null);
  const [leaving, setLeaving] = useState(false);
  const clientName = data.client_name ?? t("oauth.consent.unnamed");

  if (gone) return <Gone expired={gone === "expired"} />;

  const selectOrg = (orgId: string) => {
    const next = data.organizations.find((item) => item.org_id === orgId) ?? null;
    setOrg(next);
    setChosen(next?.scopes_grantable ?? []);
    setError(null);
  };

  const toggle = (scope: string, checked: boolean) =>
    setChosen((current) =>
      checked ? [...current, scope] : current.filter((item) => item !== scope),
    );

  const send = (approve: boolean) => {
    setError(null);
    if (approve && chosen.length === 0) {
      setError(
        org && org.scopes_grantable.length === 0
          ? t("oauth.consent.error.noGrantable")
          : t("oauth.consent.error.noScopes"),
      );
      return;
    }
    decide.mutate(
      approve ? { approve, org_id: org?.org_id ?? null, scopes: chosen } : { approve },
      {
        onSuccess: (result) => {
          if (!isSafeRedirect(result.redirect_to)) {
            setError(t("oauth.consent.error.redirect"));
            return;
          }
          setLeaving(true);
          navigate(result.redirect_to);
        },
        onError: (failure) => {
          const status = isApiError(failure) ? failure.status : null;
          if (status === 410) {
            setGone("expired");
          } else if (status === 404 || status === 409) {
            setGone("notFound"); // decyzja już zapadła (np. w innej karcie)
          } else if (isApiError(failure) && failure.code === "no_grantable_scopes") {
            setError(t("oauth.consent.error.noGrantable"));
          } else {
            const { message, requestId: rid } = describeError(failure, t);
            const id = rid ? ` (${t("app.requestId", { id: rid })})` : "";
            setError(`${t("oauth.consent.error.decision")} ${message}${id}`);
          }
        },
      },
    );
  };

  const busy = decide.isPending || leaving;
  const grantable = new Set(org?.scopes_grantable ?? []);

  return (
    <div className="view">
      <PageHeading lead={t("oauth.consent.lead")}>
        {t("oauth.consent.heading", { client: clientName })}
      </PageHeading>

      <Alert variant="info">
        <div className="stack-2">
          <p>{t("oauth.consent.selfDeclared")}</p>
          <p>
            {t("oauth.consent.redirect", { host: data.redirect_host })}{" "}
            <span className="text-muted">
              {t("oauth.consent.expires", { time: formatDateTime(data.expires_at) })}
            </span>
          </p>
        </div>
      </Alert>

      <Card>
        <div className="stack-6">
          {org === null ? (
            <Alert variant="warning" role="status">
              <p>{t("oauth.consent.noOrgs")}</p>
            </Alert>
          ) : (
            <Field id={ORG_ID} label={t("oauth.consent.org")} hint={t("oauth.consent.org.hint")}>
              {(control) => (
                <Select
                  {...control}
                  name="org_id"
                  value={org.org_id}
                  disabled={busy || data.organizations.length < 2}
                  onChange={(event) => selectOrg(event.target.value)}
                >
                  {data.organizations.map((item) => (
                    <option key={item.org_id} value={item.org_id}>
                      {t("oauth.consent.org.option", {
                        name: item.name,
                        role: t(`members.role.${item.role}`),
                      })}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}

          {org !== null ? (
            <fieldset className="fieldset" aria-describedby={`${SCOPES_ID}-hint`}>
              <legend>{t("oauth.consent.scopes")}</legend>
              <p className="hint" id={`${SCOPES_ID}-hint`}>
                {t("oauth.consent.scopes.hint")}
              </p>
              {data.scopes_requested.map((scope) => {
                const allowed = grantable.has(scope);
                return (
                  <Checkbox
                    key={scope}
                    id={`${SCOPES_ID}-${scope.replace(":", "-")}`}
                    name="scopes"
                    value={scope}
                    checked={allowed && chosen.includes(scope)}
                    disabled={!allowed || busy}
                    onChange={(event) => toggle(scope, event.target.checked)}
                    label={
                      <>
                        {td(`oauth.scope.${scope}`, scope)}{" "}
                        <code className="text-muted">{scope}</code>
                      </>
                    }
                    hint={allowed ? undefined : t("oauth.consent.scope.unavailable")}
                  />
                );
              })}
            </fieldset>
          ) : null}

          <div aria-live="assertive" role="alert">
            {error ? (
              <p className="field-error">
                <span>{error}</span>
              </p>
            ) : null}
          </div>

          {leaving ? (
            <p role="status" className="text-muted">
              {t("oauth.consent.redirecting")}
            </p>
          ) : null}

          <div className="cluster">
            {org !== null ? (
              <Button
                cta
                loading={decide.isPending && decide.variables?.approve === true}
                disabled={leaving}
                onClick={() => send(true)}
              >
                {t("oauth.consent.allow")}
              </Button>
            ) : null}
            <Button
              variant="secondary"
              loading={decide.isPending && decide.variables?.approve === false}
              disabled={leaving}
              onClick={() => send(false)}
            >
              {t("oauth.consent.deny")}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

export interface ConsentViewProps {
  /** Id prośby z adresu strony (`?request=`); null = brak parametru. */
  requestId: string | null;
  /** Organizacja wybrana domyślnie (bieżąca z sesji), o ile użytkownik w niej jest. */
  defaultOrgId: string | null;
  /** Przejście do aplikacji po decyzji (w testach podmieniane). */
  navigate?: (url: string) => void;
}

/** Ekran zgody OAuth: aplikacja (ChatGPT, Claude, ...) prosi o dostęp do organizacji w imieniu użytkownika. */
export function ConsentView({
  requestId,
  defaultOrgId,
  navigate = navigateAway,
}: ConsentViewProps) {
  const t = useT();
  const query = useOAuthRequest(requestId);

  if (!requestId) return <Gone expired={false} />;

  if (query.isPending) {
    return (
      <div className="view" aria-busy="true">
        <PageHeading>{t("oauth.consent.title")}</PageHeading>
        <p role="status" className="sr-only">
          {t("oauth.consent.loading")}
        </p>
        <Skeleton lines={5} />
      </div>
    );
  }

  if (query.error || !query.data) {
    if (isApiError(query.error) && query.error.status === 410) return <Gone expired />;
    if (isApiError(query.error) && query.error.status === 404) return <Gone expired={false} />;
    const { message, requestId: rid } = describeError(query.error, t);
    return (
      <div className="view">
        <PageHeading>{t("oauth.consent.title")}</PageHeading>
        <Alert variant="danger" role="alert" title={t("oauth.consent.error")}>
          <div className="stack-3">
            <p>{message}</p>
            {rid ? <p className="text-muted">{t("app.requestId", { id: rid })}</p> : null}
            <div>
              <Button
                variant="secondary"
                loading={query.isFetching}
                onClick={() => query.refetch()}
              >
                {t("app.retry")}
              </Button>
            </div>
          </div>
        </Alert>
      </div>
    );
  }

  return (
    <DecisionForm
      requestId={requestId}
      data={query.data}
      defaultOrgId={defaultOrgId}
      navigate={navigate}
    />
  );
}

/** Strona w ramie aplikacji (`<OrgGate>`): domyślna organizacja = bieżąca z sesji. */
export function ConsentPage({ requestId }: { requestId: string | null }) {
  const { orgId } = useSession();
  return <ConsentView requestId={requestId} defaultOrgId={orgId} />;
}

"use client";

import { Suspense, useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Alert } from "@/components/Alert";
import { Button } from "@/components/Button";
import { ErrorSummary } from "@/components/ErrorSummary";
import { Field } from "@/components/Field";
import { Input } from "@/components/Input";
import { PageHeading } from "@/components/PageHeading";
import { useAnnounce } from "@/components/Announcer";
import { useT } from "@/i18n/provider";
import { useRequestMagicLink, useVerifyMagicLink } from "@/lib/api/hooks";
import { isApiError } from "@/lib/api/client";
import { describeError } from "@/lib/errors";
import { rememberNextPath, safeNextPath, takeRememberedNextPath } from "@/lib/next-path";
import { extractToken, toSummary, validateEmail } from "@/lib/validation";
import type { Me } from "@/lib/api/types";

const EMAIL_ID = "login-email";
const TOKEN_ID = "login-token";
/** Znacznik (bez tokena) że ekran potwierdzenia był otwarty: po odświeżeniu token jest utracony. */
const CONFIRM_MARK = "daas.login.confirm";

function markConfirm(on: boolean) {
  try {
    if (on) window.sessionStorage.setItem(CONFIRM_MARK, "1");
    else window.sessionStorage.removeItem(CONFIRM_MARK);
  } catch {
    // pamięć sesji może być zablokowana: komunikat po odświeżeniu jest tylko ułatwieniem
  }
}

function LoginView() {
  const t = useT();
  const router = useRouter();
  const params = useSearchParams();
  const announce = useAnnounce();
  const requestLink = useRequestMagicLink();
  const verify = useVerifyMagicLink();

  const [email, setEmail] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);
  const [token, setToken] = useState("");
  const [tokenOpen, setTokenOpen] = useState(false);
  const autoVerified = useRef(false);
  // link otwarty w innej przeglądarce niż ta, która go zamówiła: backend żąda jawnego potwierdzenia
  // token trzymamy wyłącznie w stanie: adres jest czyszczony od razu po odczytaniu (historia, pasek adresu)
  const [pending, setPending] = useState<{ token: string; hint: string | null } | null>(null);
  const [linkLost, setLinkLost] = useState(false);

  const tokenFromUrl = params.get("token");
  // powrót po logowaniu (np. na ekran zgody OAuth): tylko ścieżki tego originu, zapamiętane na czas linku
  const nextFromUrl = safeNextPath(params.get("next"));

  useEffect(() => {
    if (nextFromUrl) rememberNextPath(nextFromUrl);
  }, [nextFromUrl]);

  const goAfterLogin = (me: Me) => {
    const next = takeRememberedNextPath() ?? nextFromUrl;
    router.replace(me.org ? (next ?? "/") : "/onboarding");
  };

  const failVerify = () => {
    setTokenOpen(true);
    setErrors({ [TOKEN_ID]: t("login.token.error.invalid") });
    setAttempt((n) => n + 1);
  };

  const verifyToken = (value: string, confirm = false) => {
    verify.mutate(
      { token: value, confirm },
      {
        onSuccess: (me) => {
          markConfirm(false);
          goAfterLogin(me);
        },
        onError: (error) => {
          if (!confirm && isApiError(error) && error.code === "confirm_required") {
            markConfirm(true);
            setPending({ token: value, hint: error.emailHint });
            return;
          }
          markConfirm(false);
          setPending(null);
          failVerify();
        },
      },
    );
  };

  const cancelConfirm = () => {
    markConfirm(false);
    setPending(null);
    setErrors({});
  };

  // link z wiadomości prowadzi na /login?token=... : weryfikujemy automatycznie (raz),
  // a token od razu przenosimy do stanu i zdejmujemy z adresu (też przy powodzeniu)
  useEffect(() => {
    if (autoVerified.current) return;
    if (tokenFromUrl) {
      autoVerified.current = true;
      router.replace("/login");
      verifyToken(tokenFromUrl);
      return;
    }
    // bez tokena w adresie: jeśli ekran potwierdzenia był otwarty, odświeżenie zgubiło token
    autoVerified.current = true;
    try {
      if (window.sessionStorage.getItem(CONFIRM_MARK)) queueMicrotask(() => setLinkLost(true));
    } catch {
      // brak dostępu do pamięci sesji
    }
    markConfirm(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tokenFromUrl]);

  // po przejściu do „Sprawdź skrzynkę” przenosimy fokus na nowy nagłówek
  useEffect(() => {
    if (sentTo || pending) document.querySelector<HTMLElement>("main h1")?.focus();
  }, [sentTo, pending]);

  const send = (target: string, resend: boolean) => {
    requestLink.mutate(target, {
      onSuccess: () => {
        setSentTo(target);
        setErrors({});
        if (resend) announce(t("login.sent.resent"));
      },
      onError: (error) => {
        const { requestId } = describeError(error, t);
        const base = t("login.error.send");
        setErrors({
          [EMAIL_ID]: requestId ? `${base} (${t("app.requestId", { id: requestId })})` : base,
        });
        setAttempt((n) => n + 1);
        setSentTo(null);
      },
    });
  };

  const onSubmitEmail = (event: FormEvent) => {
    event.preventDefault();
    const problem = validateEmail(email);
    if (problem) {
      setErrors({
        [EMAIL_ID]:
          problem === "required" ? t("login.error.emailRequired") : t("login.error.emailInvalid"),
      });
      setAttempt((n) => n + 1);
      return;
    }
    setErrors({});
    send(email.trim(), false);
  };

  const onSubmitToken = (event: FormEvent) => {
    event.preventDefault();
    const value = extractToken(token);
    if (!value) {
      setTokenOpen(true);
      setErrors({ [TOKEN_ID]: t("login.token.error.required") });
      setAttempt((n) => n + 1);
      return;
    }
    setErrors({});
    verifyToken(value);
  };

  const summary = toSummary([EMAIL_ID, TOKEN_ID], errors);

  if (pending) {
    const who = pending.hint ?? t("login.confirm.unknownAddress");
    return (
      <div className="view">
        <PageHeading lead={t("login.confirm.body", { email: who })}>
          {t("login.confirm.title")}
        </PageHeading>
        <div className="cluster">
          <Button cta loading={verify.isPending} onClick={() => verifyToken(pending.token, true)}>
            {t("login.confirm.submit", { email: who })}
          </Button>
          <Button variant="secondary" onClick={cancelConfirm}>
            {t("login.confirm.cancel")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="view">
      <PageHeading lead={sentTo ? undefined : t("login.lead")}>
        {sentTo ? t("login.sent.title") : t("login.title")}
      </PageHeading>

      {summary.length > 0 ? (
        <ErrorSummary title={t("form.summaryTitle")} errors={summary} focusKey={attempt} />
      ) : null}

      {linkLost && !sentTo ? (
        <Alert variant="warning" role="status">
          <p>{t("login.confirm.lost")}</p>
        </Alert>
      ) : null}

      {verify.isPending ? (
        <Alert variant="info" role="status">
          <p>{t("login.verifying")}</p>
        </Alert>
      ) : null}

      {sentTo ? (
        <div className="stack-4">
          <Alert variant="success">
            <p>{t("login.sent.body", { email: sentTo })}</p>
          </Alert>
          <div className="cluster">
            <Button
              variant="secondary"
              loading={requestLink.isPending}
              onClick={() => send(sentTo, true)}
            >
              {t("login.sent.resend")}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setSentTo(null);
                setErrors({});
              }}
            >
              {t("login.sent.change")}
            </Button>
          </div>
        </div>
      ) : (
        <form className="stack-6" onSubmit={onSubmitEmail} noValidate>
          <Field
            id={EMAIL_ID}
            label={t("login.email")}
            hint={t("login.email.hint")}
            error={errors[EMAIL_ID]}
            required
          >
            {(control) => (
              <Input
                {...control}
                type="email"
                name="email"
                autoComplete="email"
                inputMode="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            )}
          </Field>
          <div>
            <Button type="submit" cta loading={requestLink.isPending}>
              {requestLink.isPending ? t("login.submitting") : t("login.submit")}
            </Button>
          </div>
        </form>
      )}

      <details
        className="stack-3"
        open={tokenOpen}
        onToggle={(event) => setTokenOpen(event.currentTarget.open)}
      >
        <summary>{t("login.token.summary")}</summary>
        <form className="stack-4 mt-3" onSubmit={onSubmitToken} noValidate>
          <Field
            id={TOKEN_ID}
            label={t("login.token.label")}
            hint={t("login.token.hint")}
            error={errors[TOKEN_ID]}
          >
            {(control) => (
              <Input
                {...control}
                type="text"
                name="token"
                autoComplete="one-time-code"
                spellCheck={false}
                value={token}
                onChange={(event) => setToken(event.target.value)}
              />
            )}
          </Field>
          <div>
            <Button type="submit" variant="secondary" loading={verify.isPending}>
              {t("login.token.submit")}
            </Button>
          </div>
        </form>
      </details>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginView />
    </Suspense>
  );
}

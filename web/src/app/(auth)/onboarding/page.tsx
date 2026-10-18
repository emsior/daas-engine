"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/Button";
import { Checkbox } from "@/components/Checkbox";
import { ErrorSummary } from "@/components/ErrorSummary";
import { Field } from "@/components/Field";
import { Input, Select } from "@/components/Input";
import { PageHeading } from "@/components/PageHeading";
import { useLocale, useT } from "@/i18n/provider";
import { useCreateOrg, useMe } from "@/lib/api/hooks";
import type { Locale } from "@/lib/api/types";
import { toSummary, validateOrgName } from "@/lib/validation";

const NAME_ID = "org-name";
const LOCALE_ID = "org-locale";
const DPA_ID = "org-dpa";
const DPA_VERSION = "v1" as const;

export default function OnboardingPage() {
  const t = useT();
  const router = useRouter();
  const currentLocale = useLocale();
  const me = useMe();
  const createOrg = useCreateOrg();

  const [name, setName] = useState("");
  const [locale, setLocale] = useState<Locale>(currentLocale);
  const [dpa, setDpa] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);

  // organizacja już istnieje: onboarding nie jest potrzebny
  useEffect(() => {
    if (me.data?.org) router.replace("/");
  }, [me.data, router]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const next: Record<string, string> = {};
    if (!validateOrgName(name)) next[NAME_ID] = t("onboarding.error.name");
    if (!dpa) next[DPA_ID] = t("onboarding.error.dpa");
    setErrors(next);
    if (Object.keys(next).length > 0) {
      setAttempt((n) => n + 1);
      return;
    }
    createOrg.mutate(
      { name: name.trim(), locale, dpa_accepted: true, dpa_version: DPA_VERSION },
      {
        onSuccess: () => router.replace("/"),
        onError: () => {
          setErrors({ [NAME_ID]: t("onboarding.error.send") });
          setAttempt((n) => n + 1);
        },
      },
    );
  };

  const summary = toSummary([NAME_ID, LOCALE_ID, DPA_ID], errors);

  return (
    <div className="view">
      <PageHeading lead={t("onboarding.lead")}>{t("onboarding.title")}</PageHeading>
      {summary.length > 0 ? (
        <ErrorSummary title={t("form.summaryTitle")} errors={summary} focusKey={attempt} />
      ) : null}
      <form className="stack-6" onSubmit={onSubmit} noValidate>
        <Field
          id={NAME_ID}
          label={t("onboarding.name")}
          hint={t("onboarding.name.hint")}
          error={errors[NAME_ID]}
          required
        >
          {(control) => (
            <Input
              {...control}
              name="name"
              autoComplete="organization"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          )}
        </Field>
        <Field id={LOCALE_ID} label={t("onboarding.locale")}>
          {(control) => (
            <Select
              {...control}
              name="locale"
              value={locale}
              onChange={(event) => setLocale(event.target.value === "en" ? "en" : "pl")}
            >
              <option value="pl">{t("lang.pl")}</option>
              <option value="en">{t("lang.en")}</option>
            </Select>
          )}
        </Field>
        <div className="stack-2">
          <Checkbox
            id={DPA_ID}
            name="accept_dpa"
            checked={dpa}
            onChange={(event) => setDpa(event.target.checked)}
            invalid={Boolean(errors[DPA_ID])}
            aria-describedby={errors[DPA_ID] ? `${DPA_ID}-error` : undefined}
            aria-required="true"
            label={t("onboarding.dpa")}
          />
          {errors[DPA_ID] ? (
            <p className="field-error" id={`${DPA_ID}-error`}>
              <span>{errors[DPA_ID]}</span>
            </p>
          ) : null}
          <p>
            <Link href="/dpa" target="_blank" rel="noopener">
              {t("onboarding.dpa.link")}
            </Link>
          </p>
        </div>
        <div>
          <Button type="submit" cta loading={createOrg.isPending}>
            {t("onboarding.submit")}
          </Button>
        </div>
      </form>
    </div>
  );
}

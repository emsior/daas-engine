"use client";

import { useState, type FormEvent } from "react";
import { Alert } from "@/components/Alert";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ErrorSummary } from "@/components/ErrorSummary";
import { Field } from "@/components/Field";
import { Input, Select } from "@/components/Input";
import { PageHeading } from "@/components/PageHeading";
import { QueryState } from "@/components/QueryState";
import { Skeleton } from "@/components/Skeleton";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/Table";
import { useSession } from "@/components/shell/OrgGate";
import { useT } from "@/i18n/provider";
import { useInvite, useMembers } from "@/lib/api/hooks";
import type { Role } from "@/lib/api/types";
import { describeError } from "@/lib/errors";
import { toSummary, validateEmail } from "@/lib/validation";
import { SettingsNav } from "./SettingsNav";

const EMAIL_ID = "invite-email";
const ROLE_ID = "invite-role";

const ROLES: Role[] = ["member", "admin", "owner"];

function InviteForm({ orgId }: { orgId: string }) {
  const t = useT();
  const invite = useInvite(orgId);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);
  const [sentTo, setSentTo] = useState<string | null>(null);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setSentTo(null);
    if (validateEmail(email)) {
      setErrors({ [EMAIL_ID]: t("members.invite.error.email") });
      setAttempt((n) => n + 1);
      return;
    }
    setErrors({});
    const target = email.trim();
    invite.mutate(
      { email: target, role },
      {
        onSuccess: () => {
          setSentTo(target);
          setEmail("");
        },
        onError: (error) => {
          const { message } = describeError(error, t);
          setErrors({ [EMAIL_ID]: `${t("members.invite.error.send")} ${message}` });
          setAttempt((n) => n + 1);
        },
      },
    );
  };

  const summary = toSummary([EMAIL_ID], errors);

  return (
    <Card title={t("members.invite.title")}>
      <form className="stack-6" onSubmit={onSubmit} noValidate>
        {summary.length > 0 ? (
          <ErrorSummary title={t("form.summaryTitle")} errors={summary} focusKey={attempt} />
        ) : null}
        {sentTo ? (
          <Alert variant="success" role="status">
            <p>{t("members.invite.sent", { email: sentTo })}</p>
          </Alert>
        ) : null}
        <Field id={EMAIL_ID} label={t("members.invite.email")} error={errors[EMAIL_ID]} required>
          {(control) => (
            <Input
              {...control}
              type="email"
              name="email"
              autoComplete="off"
              inputMode="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          )}
        </Field>
        <Field id={ROLE_ID} label={t("members.invite.role")}>
          {(control) => (
            <Select
              {...control}
              name="role"
              value={role}
              onChange={(event) => setRole(event.target.value as Role)}
            >
              {ROLES.map((option) => (
                <option key={option} value={option}>
                  {t(`members.role.${option}`)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <div>
          <Button type="submit" loading={invite.isPending}>
            {t("members.invite.submit")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

export function MembersView() {
  const t = useT();
  const { orgId } = useSession();
  const members = useMembers(orgId);

  return (
    <div className="view">
      <PageHeading lead={t("members.lead")}>{t("members.title")}</PageHeading>
      <SettingsNav />
      <QueryState
        query={members}
        loadingLabel={t("members.loading")}
        errorTitle={t("members.error.title")}
        skeleton={<Skeleton lines={4} />}
        isEmpty={(data) => data.length === 0}
        empty={<p className="text-muted">{t("members.empty")}</p>}
      >
        {(data) => (
          <Table caption={t("members.caption")}>
            <Thead>
              <Tr>
                <Th>{t("members.col.email")}</Th>
                <Th>{t("members.col.name")}</Th>
                <Th>{t("members.col.role")}</Th>
              </Tr>
            </Thead>
            <Tbody>
              {data.map((member) => (
                <Tr key={member.user_id}>
                  <Td label={t("members.col.email")} className="break-all">
                    {member.email}
                  </Td>
                  <Td label={t("members.col.name")}>{member.name ?? t("app.none")}</Td>
                  <Td label={t("members.col.role")}>{t(`members.role.${member.role}`)}</Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
      </QueryState>
      <InviteForm orgId={orgId} />
    </div>
  );
}

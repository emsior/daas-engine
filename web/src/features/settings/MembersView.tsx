"use client";

import { useState, type FormEvent } from "react";
import { useAnnounce } from "@/components/Announcer";
import { Alert } from "@/components/Alert";
import { ConfirmDialog } from "@/components/ConfirmDialog";
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
import {
  useChangeMemberRole,
  useInvitations,
  useInvite,
  useMembers,
  useRemoveMember,
  useRevokeInvitation,
} from "@/lib/api/hooks";
import { formatDate } from "@/lib/format";
import type { Member, Role } from "@/lib/api/types";
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

function RoleCell({
  orgId,
  member,
  editable,
}: {
  orgId: string;
  member: Member;
  editable: boolean;
}) {
  const t = useT();
  const announce = useAnnounce();
  const change = useChangeMemberRole(orgId);
  const [error, setError] = useState<string | null>(null);
  if (!editable) return <>{t(`members.role.${member.role}`)}</>;
  return (
    <div className="stack-1">
      <Select
        aria-label={t("members.role.change", { email: member.email })}
        value={member.role}
        disabled={change.isPending}
        onChange={(event) => {
          setError(null);
          change.mutate(
            { userId: member.user_id, role: event.target.value as Role },
            {
              onSuccess: () => announce(t("members.role.changed", { email: member.email })),
              onError: (err) => setError(describeError(err, t).message),
            },
          );
        }}
      >
        {ROLES.map((option) => (
          <option key={option} value={option}>
            {t(`members.role.${option}`)}
          </option>
        ))}
      </Select>
      {error ? (
        <p className="field-error" role="alert">
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
}

function InvitationsCard({ orgId }: { orgId: string }) {
  const t = useT();
  const announce = useAnnounce();
  const invitations = useInvitations(orgId);
  const revoke = useRevokeInvitation(orgId);
  const [error, setError] = useState<string | null>(null);

  return (
    <section className="stack-3" aria-labelledby="invitations-title">
      <h2 id="invitations-title">{t("members.invitations.title")}</h2>
      <QueryState
        query={invitations}
        loadingLabel={t("members.invitations.loading")}
        errorTitle={t("members.invitations.error")}
        skeleton={<Skeleton lines={2} />}
        isEmpty={(data) => data.length === 0}
        empty={<p className="text-muted">{t("members.invitations.empty")}</p>}
      >
        {(data) => (
          <Table caption={t("members.invitations.caption")}>
            <Thead>
              <Tr>
                <Th>{t("members.col.email")}</Th>
                <Th>{t("members.col.role")}</Th>
                <Th>{t("members.invitations.col.expires")}</Th>
                <Th>{t("members.col.actions")}</Th>
              </Tr>
            </Thead>
            <Tbody>
              {data.map((invitation) => (
                <Tr key={invitation.id}>
                  <Td label={t("members.col.email")} className="break-all">
                    {invitation.email}
                  </Td>
                  <Td label={t("members.col.role")}>{t(`members.role.${invitation.role}`)}</Td>
                  <Td label={t("members.invitations.col.expires")} className="tabular">
                    {formatDate(invitation.expires_at)}
                  </Td>
                  <Td label={t("members.col.actions")}>
                    <Button
                      variant="secondary"
                      loading={revoke.isPending && revoke.variables === invitation.id}
                      onClick={() => {
                        setError(null);
                        revoke.mutate(invitation.id, {
                          onSuccess: () =>
                            announce(t("members.invitations.revoked", { email: invitation.email })),
                          onError: (err) => setError(describeError(err, t).message),
                        });
                      }}
                    >
                      {t("members.invitations.revoke")}
                      <span className="sr-only"> {invitation.email}</span>
                    </Button>
                  </Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
      </QueryState>
      {error ? (
        <p className="field-error" role="alert">
          <span>{error}</span>
        </p>
      ) : null}
    </section>
  );
}

export function MembersView() {
  const t = useT();
  const announce = useAnnounce();
  const { orgId, me } = useSession();
  const members = useMembers(orgId);
  const remove = useRemoveMember(orgId);
  const [target, setTarget] = useState<Member | null>(null);
  const myRole = me.role;
  const canManage = myRole === "owner" || myRole === "admin";
  const isOwner = myRole === "owner";

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
                {canManage ? <Th>{t("members.col.actions")}</Th> : null}
              </Tr>
            </Thead>
            <Tbody>
              {data.map((member) => {
                const self = member.user_id === me.user.id;
                const removable = canManage && !self && (member.role !== "owner" || isOwner);
                return (
                  <Tr key={member.user_id}>
                    <Td label={t("members.col.email")} className="break-all">
                      {member.email}
                    </Td>
                    <Td label={t("members.col.name")}>{member.name ?? t("app.none")}</Td>
                    <Td label={t("members.col.role")}>
                      <RoleCell orgId={orgId} member={member} editable={isOwner && !self} />
                    </Td>
                    {canManage ? (
                      <Td label={t("members.col.actions")}>
                        {removable ? (
                          <Button variant="secondary" onClick={() => setTarget(member)}>
                            {t("members.remove")}
                            <span className="sr-only"> {member.email}</span>
                          </Button>
                        ) : (
                          <span className="text-muted">{t("app.none")}</span>
                        )}
                      </Td>
                    ) : null}
                  </Tr>
                );
              })}
            </Tbody>
          </Table>
        )}
      </QueryState>

      {canManage ? <InvitationsCard orgId={orgId} /> : null}
      {canManage ? <InviteForm orgId={orgId} /> : null}

      <ConfirmDialog
        open={target !== null}
        title={t("members.remove.title", { email: target?.email ?? "" })}
        description={t("members.remove.body")}
        confirmLabel={t("members.remove.confirm")}
        pending={remove.isPending}
        error={remove.isError ? describeError(remove.error, t).message : null}
        onCancel={() => {
          setTarget(null);
          remove.reset();
        }}
        onConfirm={() => {
          if (!target) return;
          remove.mutate(target.user_id, {
            onSuccess: () => {
              announce(t("members.removed", { email: target.email }));
              setTarget(null);
            },
          });
        }}
      />
    </div>
  );
}

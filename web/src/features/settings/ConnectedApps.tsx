"use client";

import { useState } from "react";
import { useAnnounce } from "@/components/Announcer";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageHeading } from "@/components/PageHeading";
import { QueryState } from "@/components/QueryState";
import { Skeleton } from "@/components/Skeleton";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/Table";
import { useSession } from "@/components/shell/OrgGate";
import { useT } from "@/i18n/provider";
import { useMembers, useOAuthGrants, useRevokeOAuthGrant } from "@/lib/api/hooks";
import type { OAuthGrant } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";
import { SettingsNav } from "./SettingsNav";

export interface ConnectedAppsCardProps {
  orgId: string;
  /** `org:manage`: właściciel i administrator; członek widzi tylko informację. */
  canManage: boolean;
}

/** Połączone aplikacje (OAuth): kto połączył, uprawnienia, ostatnie użycie i odłączenie z potwierdzeniem. */
export function ConnectedAppsCard({ orgId, canManage }: ConnectedAppsCardProps) {
  const t = useT();
  const announce = useAnnounce();
  const grants = useOAuthGrants(orgId, canManage);
  const members = useMembers(orgId);
  const revoke = useRevokeOAuthGrant(orgId);
  const [toRevoke, setToRevoke] = useState<OAuthGrant | null>(null);

  const appName = (grant: OAuthGrant) => grant.client_name ?? t("settings.connectedApps.unnamed");
  const whoConnected = (grant: OAuthGrant) => {
    const member = members.data?.find((item) => item.user_id === grant.user_id);
    if (member && grant.role) return `${member.email} (${t(`members.role.${grant.role}`)})`;
    if (grant.role) return t(`members.role.${grant.role}`);
    return t("settings.connectedApps.formerMember");
  };

  const closeDialog = () => {
    setToRevoke(null);
    revoke.reset();
  };

  return (
    <Card title={t("settings.connectedApps.title")}>
      <div className="stack-6">
        <p>{t("settings.connectedApps.lead")}</p>
        {canManage ? (
          <QueryState
            query={grants}
            loadingLabel={t("settings.connectedApps.loading")}
            errorTitle={t("settings.connectedApps.error")}
            skeleton={<Skeleton lines={3} />}
            isEmpty={(data) => data.length === 0}
            empty={<p className="text-muted">{t("settings.connectedApps.empty")}</p>}
          >
            {(data) => (
              <Table caption={t("settings.connectedApps.caption")}>
                <Thead>
                  <Tr>
                    <Th>{t("settings.connectedApps.col.app")}</Th>
                    <Th>{t("settings.connectedApps.col.user")}</Th>
                    <Th>{t("settings.connectedApps.col.scopes")}</Th>
                    <Th>{t("settings.connectedApps.col.lastUsed")}</Th>
                    <Th>{t("settings.connectedApps.col.actions")}</Th>
                  </Tr>
                </Thead>
                <Tbody>
                  {data.map((grant) => (
                    <Tr key={grant.token_id}>
                      <Td label={t("settings.connectedApps.col.app")} className="break-all">
                        {appName(grant)}
                      </Td>
                      <Td label={t("settings.connectedApps.col.user")} className="break-all">
                        {whoConnected(grant)}
                      </Td>
                      <Td label={t("settings.connectedApps.col.scopes")}>
                        {grant.scopes.join(", ")}
                      </Td>
                      <Td label={t("settings.connectedApps.col.lastUsed")} className="tabular">
                        {grant.last_used_at
                          ? formatDateTime(grant.last_used_at)
                          : t("settings.connectedApps.never")}
                      </Td>
                      <Td label={t("settings.connectedApps.col.actions")}>
                        <div className="cell-actions">
                          <Button variant="ghost" onClick={() => setToRevoke(grant)}>
                            {t("settings.connectedApps.disconnect")}
                            <span className="sr-only">
                              {" "}
                              {t("settings.connectedApps.disconnect.sr", { app: appName(grant) })}
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
        ) : (
          <p className="text-muted">{t("settings.connectedApps.noAccess")}</p>
        )}
      </div>

      <ConfirmDialog
        open={toRevoke !== null}
        title={t("settings.connectedApps.disconnect.title", {
          app: toRevoke ? appName(toRevoke) : "",
        })}
        description={t("settings.connectedApps.disconnect.body")}
        confirmLabel={t("settings.connectedApps.disconnect")}
        pending={revoke.isPending}
        error={revoke.isError ? t("settings.connectedApps.disconnect.error") : null}
        onCancel={closeDialog}
        onConfirm={() => {
          if (toRevoke) {
            revoke.mutate(toRevoke.token_id, {
              onSuccess: () => {
                setToRevoke(null);
                announce(t("settings.connectedApps.disconnected"));
              },
            });
          }
        }}
      />
    </Card>
  );
}

/** Podstrona Ustawień: Połączone aplikacje. */
export function ConnectedAppsView() {
  const t = useT();
  const { org, me } = useSession();
  // org:manage (lista i odłączanie) mają role owner i admin
  const canManage = me.role === "owner" || me.role === "admin";
  return (
    <div className="view">
      <PageHeading>{t("settings.title")}</PageHeading>
      <SettingsNav />
      <ConnectedAppsCard orgId={org.id} canManage={canManage} />
    </div>
  );
}

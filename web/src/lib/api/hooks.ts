"use client";

/** Hooki TanStack Query per zasób. Klucze zapytań są zebrane w `qk`. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  CreateApiKeyRequest,
  CreateJobRequest,
  CreateOrgRequest,
  CreateWebhookRequest,
  InviteRequest,
  OAuthDecisionRequest,
  Role,
  ScheduleSettings,
  UpdateOrgRequest,
} from "./types";

export const qk = {
  me: ["me"] as const,
  runs: (org: string) => ["orgs", org, "runs"] as const,
  run: (org: string, id: string) => ["orgs", org, "runs", id] as const,
  evidence: (org: string) => ["orgs", org, "evidence"] as const,
  apiKeys: (org: string) => ["orgs", org, "api-keys"] as const,
  members: (org: string) => ["orgs", org, "members"] as const,
  invitations: (org: string) => ["orgs", org, "invitations"] as const,
  org: (org: string) => ["orgs", org] as const,
  usage: (org: string) => ["orgs", org, "usage"] as const,
  webhooks: (org: string) => ["orgs", org, "webhooks"] as const,
  schedule: (org: string) => ["orgs", org, "schedule"] as const,
  job: (org: string, id: string) => ["orgs", org, "jobs", id] as const,
  oauthRequest: (id: string) => ["oauth", "requests", id] as const,
  oauthGrants: (org: string) => ["orgs", org, "oauth-grants"] as const,
};

/* --- sesja --- */

export function useMe() {
  return useQuery({
    queryKey: qk.me,
    queryFn: ({ signal }) => api.me(signal),
    retry: false,
    staleTime: 60_000,
  });
}

export function useRequestMagicLink() {
  return useMutation({ mutationFn: (email: string) => api.requestMagicLink(email) });
}

export function useVerifyMagicLink() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { token: string; confirm?: boolean }) =>
      api.verifyMagicLink(vars.token, vars.confirm),
    onSuccess: (me) => client.setQueryData(qk.me, me),
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.logout(),
    onSuccess: () => client.clear(),
  });
}

export function useCreateOrg() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateOrgRequest) => api.createOrg(body),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.me }),
  });
}

/* --- raporty --- */

export function useRuns(org: string) {
  return useQuery({ queryKey: qk.runs(org), queryFn: () => api.listRuns(org) });
}

export function useRun(org: string, id: string) {
  return useQuery({ queryKey: qk.run(org, id), queryFn: () => api.getRun(org, id) });
}

export function useReportStatus(org: string, id: string) {
  return useQuery({
    queryKey: ["orgs", org, "runs", id, "report-status"] as const,
    queryFn: ({ signal }) => api.reportStatus(org, id, signal),
    staleTime: 60_000,
  });
}

export function useEvidence(org: string) {
  return useQuery({ queryKey: qk.evidence(org), queryFn: () => api.evidence(org) });
}

/* --- upload i zadania --- */

export function useUpload(org: string) {
  return useMutation({ mutationFn: (file: File) => api.upload(org, file) });
}

export function useCreateJob(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateJobRequest) => api.createJob(org, body),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.runs(org) }),
  });
}

/** Odpytywanie zadania: zapasowa ścieżka, gdy SSE przestaje działać. */
export function useJobPolling(org: string, jobId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: qk.job(org, jobId ?? "none"),
    queryFn: () => api.getJob(org, jobId ?? ""),
    enabled: enabled && jobId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "succeeded" || status === "failed" || status === "cancelled" ? false : 3000;
    },
  });
}

/* --- klucze API --- */

export function useApiKeys(org: string) {
  return useQuery({ queryKey: qk.apiKeys(org), queryFn: () => api.listApiKeys(org) });
}

export function useCreateApiKey(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateApiKeyRequest) => api.createApiKey(org, body),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.apiKeys(org) }),
  });
}

export function useRevokeApiKey(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.revokeApiKey(org, id),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.apiKeys(org) }),
  });
}

/* --- członkowie --- */

export function useMembers(org: string) {
  return useQuery({ queryKey: qk.members(org), queryFn: () => api.listMembers(org) });
}

export function useInvite(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: InviteRequest) => api.invite(org, body),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.invitations(org) }),
  });
}

export function useInvitations(org: string) {
  return useQuery({ queryKey: qk.invitations(org), queryFn: () => api.listInvitations(org) });
}

export function useRevokeInvitation(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.revokeInvitation(org, id),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.invitations(org) }),
  });
}

export function useChangeMemberRole(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { userId: string; role: Role }) =>
      api.changeMemberRole(org, vars.userId, vars.role),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.members(org) }),
  });
}

export function useRemoveMember(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => api.removeMember(org, userId),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.members(org) }),
  });
}

/* --- ustawienia --- */

export function useOrg(org: string) {
  return useQuery({ queryKey: qk.org(org), queryFn: () => api.getOrg(org) });
}

export function useUpdateOrg(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateOrgRequest) => api.updateOrg(org, body),
    onSuccess: (updated) => {
      client.setQueryData(qk.org(org), updated);
      return client.invalidateQueries({ queryKey: qk.me });
    },
  });
}

export function useUsage(org: string) {
  return useQuery({ queryKey: qk.usage(org), queryFn: () => api.usage(org) });
}

export function useWebhooks(org: string) {
  return useQuery({ queryKey: qk.webhooks(org), queryFn: () => api.listWebhooks(org) });
}

export function useCreateWebhook(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateWebhookRequest) => api.createWebhook(org, body),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.webhooks(org) }),
  });
}

export function useDeleteWebhook(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteWebhook(org, id),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.webhooks(org) }),
  });
}

export function usePingWebhook(org: string) {
  return useMutation({ mutationFn: (id: string) => api.pingWebhook(org, id) });
}

/* --- harmonogram i powiadomienia --- */

export function useSchedule(org: string) {
  return useQuery({ queryKey: qk.schedule(org), queryFn: () => api.getSchedule(org) });
}

export function useUpdateSchedule(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ScheduleSettings) => api.updateSchedule(org, body),
    onSuccess: (saved) => client.setQueryData(qk.schedule(org), saved),
  });
}

/* --- OAuth: zgoda i połączone aplikacje --- */

/** Prośba o dostęp jest jednorazowa: bez ponawiania (404/410 to stan, nie awaria) i bez odświeżania po decyzji. */
export function useOAuthRequest(id: string | null) {
  return useQuery({
    queryKey: qk.oauthRequest(id ?? "none"),
    queryFn: ({ signal }) => api.getOAuthRequest(id ?? "", signal),
    enabled: id !== null,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  });
}

export function useDecideOAuthRequest(id: string) {
  return useMutation({
    mutationFn: (body: OAuthDecisionRequest) => api.decideOAuthRequest(id, body),
  });
}

export function useOAuthGrants(org: string, enabled = true) {
  return useQuery({
    queryKey: qk.oauthGrants(org),
    queryFn: () => api.listOAuthGrants(org),
    enabled,
  });
}

export function useRevokeOAuthGrant(org: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (tokenId: string) => api.revokeOAuthGrant(org, tokenId),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.oauthGrants(org) }),
  });
}

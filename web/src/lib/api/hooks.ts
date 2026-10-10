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
  UpdateOrgRequest,
} from "./types";

export const qk = {
  me: ["me"] as const,
  runs: (org: string) => ["orgs", org, "runs"] as const,
  run: (org: string, id: string) => ["orgs", org, "runs", id] as const,
  evidence: (org: string) => ["orgs", org, "evidence"] as const,
  apiKeys: (org: string) => ["orgs", org, "api-keys"] as const,
  members: (org: string) => ["orgs", org, "members"] as const,
  org: (org: string) => ["orgs", org] as const,
  usage: (org: string) => ["orgs", org, "usage"] as const,
  webhooks: (org: string) => ["orgs", org, "webhooks"] as const,
  job: (org: string, id: string) => ["orgs", org, "jobs", id] as const,
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
    mutationFn: (token: string) => api.verifyMagicLink(token),
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
      return status === "succeeded" || status === "failed" || status === "cancelled" ? false : 2000;
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

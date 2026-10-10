/**
 * Zgodność kokpitu z kontraktem backendu: `docs/openapi.json` (eksport z aplikacji) -> `schema.d.ts`.
 * - testy typów (kompilacja `tsc`): typy żądań kokpitu muszą pasować do schematów żądań backendu,
 * - test środowiska wykonania: każda ścieżka z `paths` w `client.ts` istnieje w OpenAPI.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { paths } from "./client";
import type { components } from "./schema";
import type {
  ApiKey,
  CreateApiKeyRequest,
  CreateJobRequest,
  CreateOrgRequest,
  CreateWebhookRequest,
  InviteRequest,
  Job,
  Member,
  Org,
  UpdateOrgRequest,
  VerifyResult,
} from "./types";

type S = components["schemas"];

/* --- typy żądań: kokpit -> backend (błąd kompilacji = rozjazd nazw lub typów pól) --- */
type KeysIn<A, B> = Exclude<keyof A, keyof B> extends never ? true : never;
type Sendable<A, B> = A extends Partial<B> ? KeysIn<A, B> : never;

const requests: [
  Sendable<CreateOrgRequest, S["OrgCreate"]>,
  Sendable<UpdateOrgRequest, S["OrgPatch"]>,
  Sendable<CreateApiKeyRequest, S["ApiKeyCreate"]>,
  Sendable<CreateJobRequest, S["JobCreate"]>,
  Sendable<InviteRequest, S["InvitationCreate"]>,
  Sendable<CreateWebhookRequest, S["WebhookCreate"]>,
] = [true, true, true, true, true, true];

/* --- typy odpowiedzi czytane bez adaptera: backend -> kokpit (pola z domyślną wartością są zawsze obecne) --- */
const responses = {
  key: {} as Required<S["ApiKeyOut"]> satisfies ApiKey,
  job: {} as Required<S["JobOut"]> satisfies Job,
  member: {} as Required<S["MemberOut"]> satisfies Omit<Member, never>,
  org: {} as Required<S["OrgOut"]> satisfies Org,
  verify: {} as Required<S["VerifyOut"]> satisfies VerifyResult & { known: boolean },
};

function openapiPaths(): Set<string> {
  const file = path.resolve(import.meta.dirname, "../../../../docs/openapi.json");
  const spec = JSON.parse(readFileSync(file, "utf8")) as { paths: Record<string, unknown> };
  const normalized = Object.keys(spec.paths)
    .filter((p) => p.startsWith("/api/v1/"))
    .map((p) =>
      p
        .replace("/api/v1", "")
        .replace("{org_id}", "ORG")
        .replace(/\{[a-z_]+\}/g, "ID"),
    );
  return new Set(normalized);
}

describe("kontrakt API (docs/openapi.json)", () => {
  it("typy żądań i odpowiedzi kompilują się względem schematów backendu", () => {
    expect(requests).toHaveLength(6);
    expect(Object.keys(responses)).toHaveLength(5);
  });

  it("każda ścieżka z client.ts istnieje w OpenAPI backendu", () => {
    const known = openapiPaths();
    const used = [
      paths.me(),
      paths.magicLink(),
      paths.magicLinkVerify(),
      paths.logout(),
      paths.orgs(),
      paths.org("ORG"),
      paths.members("ORG"),
      paths.invitations("ORG"),
      paths.invitation("ORG", "ID"),
      paths.member("ORG", "ID"),
      paths.apiKeys("ORG"),
      paths.apiKey("ORG", "ID"),
      paths.uploads("ORG"),
      paths.jobs("ORG"),
      paths.job("ORG", "ID"),
      paths.jobEvents("ORG", "ID"),
      paths.jobCancel("ORG", "ID"),
      paths.runs("ORG"),
      paths.run("ORG", "ID"),
      paths.runReport("ORG", "ID"),
      paths.verify("ORG"),
      paths.evidence("ORG"),
      paths.usage("ORG"),
      paths.webhooks("ORG"),
      paths.webhook("ORG", "ID"),
      paths.webhookPing("ORG", "ID"),
    ];
    const missing = used.filter((p) => !known.has(p));
    expect(missing).toEqual([]);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, paths, reportUrl, request } from "./client";

function jsonResponse(body: unknown, init: ResponseInit & { problem?: boolean } = {}): Response {
  const { problem, ...rest } = init;
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": problem ? "application/problem+json" : "application/json" },
    ...rest,
  });
}

const USER = { id: "u1", email: "a@example.test", name: null, locale: "pl" };

describe("client API", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    document.cookie = "daas_csrf=; Max-Age=0; Path=/";
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("parsuje problem+json do ApiError (status, title, detail, requestId)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          title: "Plik za duży",
          status: 413,
          detail: "Limit 10 MiB",
          request_id: "req-42",
          code: "file_too_large",
        },
        { status: 413, problem: true },
      ),
    );
    const error = await request("/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(413);
    expect(apiError.title).toBe("Plik za duży");
    expect(apiError.detail).toBe("Limit 10 MiB");
    expect(apiError.requestId).toBe("req-42");
    expect(apiError.code).toBe("file_too_large");
  });

  it("bierze requestId z nagłówka, gdy ciało go nie ma", async () => {
    fetchMock.mockResolvedValue(
      new Response("<html>502</html>", {
        status: 502,
        statusText: "Bad Gateway",
        headers: { "x-request-id": "hdr-1" },
      }),
    );
    const error = (await request("/x").catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(502);
    expect(error.requestId).toBe("hdr-1");
    expect(error.title).toBe("Bad Gateway");
  });

  it("dodaje X-CSRF-Token z ciasteczka daas_csrf do mutacji", async () => {
    document.cookie = "daas_csrf=tok123; Path=/";
    fetchMock.mockResolvedValue(jsonResponse({}, { status: 201 }));
    await api.createWebhook("org1", { url: "https://a.example/h", events: ["run.finished"] });
    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect((init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok123");
    expect(init?.method).toBe("POST");
  });

  it("nie wysyła tokena CSRF w żądaniach GET", async () => {
    document.cookie = "daas_csrf=tok123; Path=/";
    fetchMock.mockResolvedValue(jsonResponse([]));
    await api.listRuns("org1");
    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect((init?.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
  });

  it("używa credentials: include i adresu z prefiksem /api/v1", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ principal: { type: "user", org_id: null, scopes: [] }, user: USER, orgs: [] }),
    );
    await api.me();
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toContain("/api/v1/me");
    expect(init?.credentials).toBe("include");
  });

  it("me: łączy /me z pełną organizacją z /orgs/{id}", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          principal: { type: "user", org_id: null, scopes: [] },
          user: USER,
          orgs: [{ org_id: "o1", org_name: "Firma", org_slug: "firma", role: "owner" }],
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ id: "o1", name: "Firma", retention_days: 90 }));
    const me = await api.me();
    expect(String(fetchMock.mock.calls[1]?.[0])).toMatch(/\/api\/v1\/orgs\/o1$/);
    expect(me.org?.id).toBe("o1");
    expect(me.role).toBe("owner");
    expect(me.user.email).toBe("a@example.test");
  });

  it("me: użytkownik bez organizacji ma org = null i nie woła /orgs", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ principal: { type: "user", org_id: null, scopes: [] }, user: USER, orgs: [] }),
    );
    const me = await api.me();
    expect(me.org).toBeNull();
    expect(me.role).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("verifyMagicLink: wymienia token na sesję, potem pobiera profil", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ user: USER, orgs: [], csrf_token: "c", expires_at: "x" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          principal: { type: "user", org_id: null, scopes: [] },
          user: USER,
          orgs: [],
        }),
      );
    const me = await api.verifyMagicLink("t".repeat(30));
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/\/auth\/magic-link\/verify$/);
    expect(me.org).toBeNull();
  });

  it("listRuns czyta stronę {items} i mapuje skróty (bez null)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        items: [
          {
            run_id: "r1",
            pipeline: "ecommerce_demo",
            status: "SUCCESS",
            created_at: "2026-10-10T01:00:00Z",
            job_id: "j1",
            report_sha256: { html: "a".repeat(64), md: null, json: "b".repeat(64) },
            links: {},
          },
        ],
        next_cursor: null,
      }),
    );
    const runs = await api.listRuns("o1");
    expect(runs).toHaveLength(1);
    expect(runs[0]?.report_sha256).toEqual({ html: "a".repeat(64), json: "b".repeat(64) });
    expect(runs[0]?.metrics).toEqual({});
  });

  it("getRun przenosi metryki skalarne i liczbę rekordów", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        run_id: "r1",
        pipeline: "ecommerce_demo",
        status: "SUCCESS",
        created_at: "2026-10-10T01:00:00Z",
        report_sha256: {},
        records_processed: 12,
        metrics: { revenue_total: 7255.95, nested: { a: 1 }, ok: true },
      }),
    );
    const run = await api.getRun("o1", "r1");
    expect(run.records_processed).toBe(12);
    expect(run.metrics).toEqual({ revenue_total: 7255.95, ok: true });
  });

  it("upload: mapuje UploadOut (id -> upload_id), paczka bez mappingu jest gotowa", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          id: "u1",
          kind: "package",
          filename: "host.json",
          bytes: 10,
          sha256: "c".repeat(64),
          created_at: "x",
          retention_until: "y",
          suggested_pipeline: "uksc_evidence",
        },
        { status: 201 },
      ),
    );
    const preview = await api.upload("o1", new File(["{}"], "host.json"));
    expect(preview.upload_id).toBe("u1");
    expect(preview.ready).toBe(true);
    expect(preview.mapping).toEqual({});
    expect(preview.suggested_pipeline).toBe("uksc_evidence");
  });

  it("evidence: checks -> controls; 404 = pusty raport", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        run_id: "r1",
        host: "WS-01",
        collected_at: null,
        checks: [
          {
            control_id: "ENC-01",
            requirement: "art. 8",
            title: "Szyfrowanie",
            status: "PASS",
            artifact: { sha256: "d".repeat(64), source: "powershell", collected_at: "z" },
          },
        ],
      }),
    );
    const report = await api.evidence("o1");
    expect(report.controls[0]?.control_id).toBe("ENC-01");
    expect(report.controls[0]?.artifact?.sha256).toBe("d".repeat(64));

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ title: "Not Found" }, { status: 404, problem: true }),
    );
    expect((await api.evidence("o1")).controls).toEqual([]);
  });

  it("usage i ping webhooka: mapowanie pól backendu", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        org_id: "o1",
        plan: "free",
        retention_days: 90,
        current: { period: "2026-10", runs: 3, upload_bytes: 2048 },
        previous: { period: "2026-09", runs: 0, upload_bytes: 0 },
        limits: { runs_per_month: 20, upload_bytes_per_month: null },
      }),
    );
    expect(await api.usage("o1")).toMatchObject({
      period: "2026-10",
      runs: 3,
      runs_limit: 20,
      upload_bytes_limit: null,
    });
    fetchMock.mockResolvedValueOnce(jsonResponse({ delivered: true, status: 204 }));
    expect(await api.pingWebhook("o1", "w1")).toEqual({ ok: true, status_code: 204 });
  });

  it("błędy walidacji FastAPI ({loc, msg}) trafiają do fieldErrors", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          title: "Unprocessable Entity",
          status: 422,
          code: "validation_error",
          errors: [{ loc: ["body", "name"], msg: "Field required", type: "missing" }],
        },
        { status: 422, problem: true },
      ),
    );
    const error = (await request("/x").catch((e: unknown) => e)) as ApiError;
    expect(error.fieldErrors).toEqual([{ field: "name", message: "Field required" }]);
  });

  it("zamienia błąd sieci na ApiError ze statusem 0", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = (await request("/x").catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.isNetwork).toBe(true);
  });

  it("zwraca undefined dla 204", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.logout()).resolves.toBeUndefined();
  });

  it("wysyła upload jako multipart bez ręcznego Content-Type", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ upload_id: "u" }, { status: 201 }));
    await api.upload("org1", new File(["a;b"], "x.csv", { type: "text/csv" }));
    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("buduje adresy z jednego miejsca mapowania ścieżek", () => {
    expect(paths.runReport("o", "r")).toBe("/orgs/o/runs/r/report");
    expect(reportUrl("o", "r", "html")).toMatch(/\/api\/v1\/orgs\/o\/runs\/r\/report\?fmt=html$/);
  });
});

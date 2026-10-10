import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, paths, reportUrl, request } from "./client";

function jsonResponse(body: unknown, init: ResponseInit & { problem?: boolean } = {}): Response {
  const { problem, ...rest } = init;
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": problem ? "application/problem+json" : "application/json" },
    ...rest,
  });
}

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
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    await api.me();
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toContain("/api/v1/me");
    expect(init?.credentials).toBe("include");
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

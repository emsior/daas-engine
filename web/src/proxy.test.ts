import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "./proxy";

describe("proxy: sprawdzenie sesji w API", () => {
  const fetchMock = vi.fn<typeof fetch>();
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("woła GET /api/v1/me z ciasteczkiem oraz x-forwarded-for i x-forwarded-proto klienta", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("https://app.example.test/upload", {
      headers: {
        cookie: "daas_session=abc",
        "x-forwarded-for": "198.51.100.4, 172.18.0.3",
        "x-forwarded-proto": "https",
      },
    });
    await proxy(request);
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toMatch(/\/api\/v1\/me$/);
    const headers = init?.headers as Record<string, string>;
    expect(headers["x-forwarded-for"]).toBe("198.51.100.4");
    expect(headers["x-forwarded-proto"]).toBe("https");
    expect(headers.cookie).toBe("daas_session=abc");
  });

  it("przekierowuje na /login przy 401 (zachowanie bez zmian)", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    const response = await proxy(new NextRequest("https://app.example.test/upload"));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("https://app.example.test/login");
  });

  it("ze strony zgody OAuth przekierowuje na /login z parametrem next (ścieżka i zapytanie)", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    const response = await proxy(
      new NextRequest("https://app.example.test/oauth/consent?request=01ARZ3NDEKTSV4RRFFQ69G5FAV"),
    );
    expect(response.status).toBe(307);
    const location = new URL(response.headers.get("location") ?? "");
    expect(location.origin + location.pathname).toBe("https://app.example.test/login");
    expect(location.searchParams.get("next")).toBe(
      "/oauth/consent?request=01ARZ3NDEKTSV4RRFFQ69G5FAV",
    );
  });
});

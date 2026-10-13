import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/utils";
import { UploadWizard } from "./UploadWizard";

vi.mock("@/components/shell/OrgGate", () => ({
  useSession: () => ({ orgId: "ORG1", role: "owner", email: "owner@example.test", orgName: "Acme" }),
}));

// SSE nie istnieje w jsdom: łącznik nic nie robi, a stan zadania pozostaje "w kolejce"
vi.mock("@/lib/sse", () => ({ connectJobEvents: () => () => undefined }));

const PREVIEW = {
  id: "u1",
  kind: "csv",
  filename: "dane.csv",
  bytes: 64,
  sha256: "a".repeat(64),
  created_at: "2026-10-12T08:00:00Z",
  retention_until: "2027-01-10T08:00:00Z",
  suggested_pipeline: "ecommerce_demo",
  rows: 12,
  columns: ["Data sprzedaży", "Wartość brutto"],
  mapping: { date: "Data sprzedaży", revenue: "Wartość brutto" },
  unmapped_columns: [],
  generated: [],
  warnings: [],
  confidence: 0.9,
  ready: true,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderWizard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithProviders(
    <QueryClientProvider client={client}>
      <UploadWizard />
    </QueryClientProvider>,
  );
}

async function uploadFile(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>) {
  const user = userEvent.setup();
  renderWizard();
  const input = document.getElementById("upload-file") as HTMLInputElement;
  await user.upload(input, new File(["Data sprzedaży;Wartość brutto\n01.08.2026;10,00\n"], "dane.csv"));
  await user.click(screen.getByRole("button", { name: "Sprawdź plik" }));
  await screen.findByRole("button", { name: "Uruchom raport" });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  return user;
}

function calledUrls(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>): string[] {
  return fetchMock.mock.calls.map(([input]) => String(input instanceof Request ? input.url : input));
}

describe("UploadWizard: zadanie zakolejkowane przez harmonogram (auto_job_id)", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("śledzi zadanie z auto_job_id i nie wywołuje POST /jobs", async () => {
    fetchMock.mockImplementation(async (input) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.includes("/uploads")) return json({ ...PREVIEW, auto_job_id: "01JOBAUTO" }, 201);
      throw new Error(`unexpected request ${url}`);
    });
    const user = await uploadFile(fetchMock);

    await user.click(screen.getByRole("button", { name: "Uruchom raport" }));

    expect((await screen.findAllByText("Zadanie czeka w kolejce…")).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Anuluj zadanie" })).toBeInTheDocument();
    expect(calledUrls(fetchMock).some((url) => url.includes("/jobs"))).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("bez auto_job_id tworzy zadanie przez POST /jobs", async () => {
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.includes("/uploads")) return json({ ...PREVIEW, auto_job_id: null }, 201);
      if (url.endsWith("/jobs") && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { pipeline: string; upload_id: string };
        expect(body).toEqual({ pipeline: "ecommerce_demo", upload_id: "u1" });
        return json({ id: "01JOBMANUAL", status: "queued", pipeline: "ecommerce_demo" }, 202);
      }
      throw new Error(`unexpected request ${url}`);
    });
    const user = await uploadFile(fetchMock);

    await user.click(screen.getByRole("button", { name: "Uruchom raport" }));

    await waitFor(() => expect(calledUrls(fetchMock).filter((u) => u.endsWith("/jobs"))).toHaveLength(1));
    expect((await screen.findAllByText("Zadanie czeka w kolejce…")).length).toBeGreaterThan(0);
  });
});

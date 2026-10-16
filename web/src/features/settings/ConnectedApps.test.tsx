import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Member, OAuthGrant } from "@/lib/api/types";
import { renderWithProviders } from "@/test/utils";
import { ConnectedAppsCard } from "./ConnectedApps";

const GRANT: OAuthGrant = {
  token_id: "TOKEN1",
  client_id: "client-1",
  client_name: "Synthetic Connector",
  user_id: "USER1",
  role: "owner",
  scopes: ["runs:read", "reports:read"],
  created_at: "2026-10-12T08:00:00Z",
  last_used_at: "2026-10-13T07:05:00Z",
  expires_at: "2026-11-12T08:00:00Z",
};
const UNUSED: OAuthGrant = {
  ...GRANT,
  token_id: "TOKEN2",
  client_id: "client-2",
  client_name: null,
  user_id: "USER9",
  role: null,
  last_used_at: null,
};
const MEMBERS: Member[] = [
  { user_id: "USER1", email: "owner@example.test", name: null, role: "owner", created_at: null },
];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function renderCard(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithProviders(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("ConnectedAppsCard", () => {
  const fetchMock = vi.fn<typeof fetch>();
  let grants: OAuthGrant[] = [];

  beforeEach(() => {
    grants = [GRANT, UNUSED];
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/members")) return json(MEMBERS);
      if (init?.method === "DELETE") {
        grants = grants.filter((grant) => !url.endsWith(`/oauth/grants/${grant.token_id}`));
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/oauth/grants")) return json(grants);
      return json({ status: 404 }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("pokazuje aplikację, kto ją połączył, uprawnienia i ostatnie użycie", async () => {
    renderCard(<ConnectedAppsCard orgId="ORG1" canManage />);
    const table = await screen.findByRole("table");
    const [, first, second] = within(table).getAllByRole("row");
    expect(first).toHaveTextContent("Synthetic Connector");
    await waitFor(() => expect(first).toHaveTextContent("owner@example.test (Właściciel)"));
    expect(first).toHaveTextContent("runs:read, reports:read");
    expect(first).toHaveTextContent("13.10.2026");
    expect(second).toHaveTextContent("Aplikacja bez nazwy");
    expect(second).toHaveTextContent("osoba spoza organizacji");
    expect(second).toHaveTextContent("jeszcze nie");
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).endsWith("/orgs/ORG1/oauth/grants")),
    ).toBe(true);
  });

  it("odłącza aplikację po potwierdzeniu i odświeża listę", async () => {
    const user = userEvent.setup();
    renderCard(<ConnectedAppsCard orgId="ORG1" canManage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: /Odłącz.*aplikację Synthetic Connector/ }));

    const dialog = screen.getByRole("alertdialog", {
      name: "Odłączyć aplikację Synthetic Connector?",
    });
    expect(dialog).toHaveTextContent(/straci dostęp/);
    await user.click(within(dialog).getByRole("button", { name: "Odłącz" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "DELETE");
    expect(String(call?.[0])).toMatch(/\/orgs\/ORG1\/oauth\/grants\/TOKEN1$/);
    await waitFor(() => expect(screen.queryByText("Synthetic Connector")).not.toBeInTheDocument());
    expect(screen.getByTestId("live-region")).toHaveTextContent("Aplikacja odłączona.");
  });

  it("anulowanie nie odłącza", async () => {
    const user = userEvent.setup();
    renderCard(<ConnectedAppsCard orgId="ORG1" canManage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: /Odłącz.*aplikację Synthetic Connector/ }));
    await user.click(screen.getByRole("button", { name: "Anuluj" }));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    expect(screen.getByText("Synthetic Connector")).toBeInTheDocument();
  });

  it("błąd odłączenia zostaje w oknie potwierdzenia", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/members")) return json(MEMBERS);
      if (init?.method === "DELETE") return json({ status: 500, code: "internal_error" }, 500);
      return json([GRANT]);
    });
    renderCard(<ConnectedAppsCard orgId="ORG1" canManage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: /Odłącz.*aplikację Synthetic Connector/ }));
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Odłącz" }),
    );
    expect(
      await screen.findByText("Nie udało się odłączyć aplikacji. Spróbuj ponownie."),
    ).toBeInTheDocument();
  });

  it("pusta lista i błąd pobrania mają własne stany", async () => {
    grants = [];
    const { unmount } = renderCard(<ConnectedAppsCard orgId="ORG1" canManage />);
    expect(await screen.findByText("Żadna aplikacja nie jest połączona.")).toBeInTheDocument();
    unmount();
    fetchMock.mockImplementation(async () => json({ status: 500, code: "internal_error" }, 500));
    renderCard(<ConnectedAppsCard orgId="ORG1" canManage />);
    expect(
      await screen.findByText("Nie udało się pobrać połączonych aplikacji"),
    ).toBeInTheDocument();
  });

  it("członek bez org:manage widzi tylko informację i nie pyta API o listę", async () => {
    renderCard(<ConnectedAppsCard orgId="ORG1" canManage={false} />);
    expect(
      screen.getByText(
        "Połączonymi aplikacjami zarządza właściciel albo administrator organizacji.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/oauth/grants"))).toBe(false);
  });
});

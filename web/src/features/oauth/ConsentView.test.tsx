import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OAuthRequest } from "@/lib/api/types";
import { renderWithProviders } from "@/test/utils";
import { ConsentView, isSafeRedirect } from "./ConsentView";

const REQUEST_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
const REQUEST: OAuthRequest = {
  request_id: REQUEST_ID,
  client_id: "client-1",
  client_name: "Synthetic Connector",
  scopes_requested: ["runs:read", "uploads:write", "org:manage"],
  redirect_host: "agent.example.test",
  expires_at: "2026-10-13T10:10:00Z",
  organizations: [
    {
      org_id: "ORG1",
      name: "Acme",
      role: "owner",
      scopes_grantable: ["runs:read", "uploads:write", "org:manage"],
    },
    {
      org_id: "ORG2",
      name: "Druga",
      role: "member",
      scopes_grantable: ["runs:read", "uploads:write"],
    },
  ],
};
const BACK = "https://agent.example.test/oauth/callback?tenant=1";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function renderView(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithProviders(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("ConsentView", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const navigate = vi.fn<(url: string) => void>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    fetchMock.mockReset();
    navigate.mockReset();
    vi.unstubAllGlobals();
  });

  const decisionBody = (call = 1) => JSON.parse(String(fetchMock.mock.calls[call]?.[1]?.body));

  it("pokazuje aplikację, adres powrotu, organizację z sesji i uprawnienia według roli", async () => {
    fetchMock.mockResolvedValue(json(REQUEST));
    renderView(<ConsentView requestId={REQUEST_ID} defaultOrgId="ORG2" navigate={navigate} />);

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Aplikacja Synthetic Connector prosi o dostęp",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Po decyzji wrócisz do: agent\.example\.test/)).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(
      /\/oauth\/requests\/01ARZ3NDEKTSV4RRFFQ69G5FAV$/,
    );
    expect(screen.getByLabelText(/Organizacja/)).toHaveValue("ORG2");
    expect(screen.getByRole("checkbox", { name: /Odczyt zadań/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Wgrywanie plików/ })).toBeChecked();
    const manage = screen.getByRole("checkbox", { name: /Zmiana ustawień organizacji/ });
    expect(manage).toBeDisabled();
    expect(manage).not.toBeChecked();
    expect(screen.getByText("Niedostępne dla Twojej roli w tej organizacji")).toBeInTheDocument();
  });

  it("zgoda wysyła organizację i zaznaczone uprawnienia, potem wraca do aplikacji", async () => {
    const user = userEvent.setup();
    fetchMock
      .mockResolvedValueOnce(json(REQUEST))
      .mockResolvedValueOnce(json({ redirect_to: `${BACK}&code=abc&state=xyz` }));
    renderView(<ConsentView requestId={REQUEST_ID} defaultOrgId="ORG1" navigate={navigate} />);

    await user.click(await screen.findByRole("checkbox", { name: /Wgrywanie plików/ }));
    await user.click(screen.getByRole("button", { name: "Zezwól" }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith(`${BACK}&code=abc&state=xyz`));
    const [url, init] = fetchMock.mock.calls[1] ?? [];
    expect(String(url)).toMatch(/\/oauth\/requests\/01ARZ3NDEKTSV4RRFFQ69G5FAV\/decision$/);
    expect(init?.method).toBe("POST");
    expect(decisionBody()).toEqual({
      approve: true,
      org_id: "ORG1",
      scopes: ["runs:read", "org:manage"],
    });
    expect(screen.getByText("Wracam do aplikacji…")).toBeInTheDocument();
  });

  it("zmiana organizacji przywraca uprawnienia możliwe w nowej roli", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(json(REQUEST));
    renderView(<ConsentView requestId={REQUEST_ID} defaultOrgId="ORG1" navigate={navigate} />);

    const manage = await screen.findByRole("checkbox", { name: /Zmiana ustawień organizacji/ });
    expect(manage).toBeChecked();
    await user.selectOptions(screen.getByLabelText(/Organizacja/), "ORG2");
    expect(manage).toBeDisabled();
    expect(manage).not.toBeChecked();
    expect(screen.getByRole("option", { name: "Druga (Członek)" })).toBeInTheDocument();
  });

  it("bez zaznaczonych uprawnień nie wysyła zgody i ogłasza błąd", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(json(REQUEST));
    renderView(<ConsentView requestId={REQUEST_ID} defaultOrgId="ORG2" navigate={navigate} />);

    await user.click(await screen.findByRole("checkbox", { name: /Odczyt zadań/ }));
    await user.click(screen.getByRole("checkbox", { name: /Wgrywanie plików/ }));
    await user.click(screen.getByRole("button", { name: "Zezwól" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Zaznacz co najmniej jedno uprawnienie.");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("odmowa wysyła tylko decyzję i wraca do aplikacji z błędem access_denied", async () => {
    const user = userEvent.setup();
    const denied = `${BACK}&error=access_denied&state=xyz`;
    fetchMock
      .mockResolvedValueOnce(json(REQUEST))
      .mockResolvedValueOnce(json({ redirect_to: denied }));
    renderView(<ConsentView requestId={REQUEST_ID} defaultOrgId="ORG1" navigate={navigate} />);

    await user.click(await screen.findByRole("button", { name: "Odmów" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(denied));
    expect(decisionBody()).toEqual({ approve: false });
  });

  it("wygasła prośba (410) kieruje z powrotem do aplikacji", async () => {
    fetchMock.mockResolvedValue(json({ status: 410, code: "expired" }, 410));
    renderView(<ConsentView requestId={REQUEST_ID} defaultOrgId="ORG1" navigate={navigate} />);
    expect(await screen.findByText("Prośba o dostęp wygasła")).toBeInTheDocument();
    expect(screen.getByText(/Wróć do aplikacji/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zezwól" })).not.toBeInTheDocument();
  });

  it("nieznana prośba (404) i brak parametru pokazują stan „nie znaleziono”", async () => {
    fetchMock.mockResolvedValue(json({ status: 404, code: "not_found" }, 404));
    const { unmount } = renderView(
      <ConsentView requestId={REQUEST_ID} defaultOrgId="ORG1" navigate={navigate} />,
    );
    expect(await screen.findByText("Nie znaleziono prośby o dostęp")).toBeInTheDocument();
    unmount();
    fetchMock.mockReset();
    renderView(<ConsentView requestId={null} defaultOrgId="ORG1" navigate={navigate} />);
    expect(screen.getByText("Nie znaleziono prośby o dostęp")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("decyzja po wygaśnięciu (410) zamienia formularz na komunikat", async () => {
    const user = userEvent.setup();
    fetchMock
      .mockResolvedValueOnce(json(REQUEST))
      .mockResolvedValueOnce(json({ status: 410, code: "expired" }, 410));
    renderView(<ConsentView requestId={REQUEST_ID} defaultOrgId="ORG1" navigate={navigate} />);
    await user.click(await screen.findByRole("button", { name: "Zezwól" }));
    expect(await screen.findByText("Prośba o dostęp wygasła")).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("błąd sieci przy decyzji zostaje na ekranie w regionie ogłoszeń", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(json(REQUEST)).mockRejectedValueOnce(new TypeError("offline"));
    renderView(<ConsentView requestId={REQUEST_ID} defaultOrgId="ORG1" navigate={navigate} />);
    await user.click(await screen.findByRole("button", { name: "Zezwól" }));
    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(alert).toHaveTextContent("Nie udało się zapisać decyzji."));
    expect(alert).toHaveAttribute("aria-live", "assertive");
    expect(navigate).not.toHaveBeenCalled();
  });

  it("błąd pobrania pokazuje stan błędu z ponowieniem", async () => {
    fetchMock.mockResolvedValue(json({ status: 500, code: "internal_error" }, 500));
    renderView(<ConsentView requestId={REQUEST_ID} defaultOrgId="ORG1" navigate={navigate} />);
    expect(await screen.findByText("Nie udało się pobrać prośby o dostęp")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Spróbuj ponownie" })).toBeInTheDocument();
  });

  it("nie przechodzi pod adres innego schematu niż http(s)", async () => {
    const user = userEvent.setup();
    fetchMock
      .mockResolvedValueOnce(json(REQUEST))
      .mockResolvedValueOnce(json({ redirect_to: "javascript:alert(1)" }));
    renderView(<ConsentView requestId={REQUEST_ID} defaultOrgId="ORG1" navigate={navigate} />);
    await user.click(await screen.findByRole("button", { name: "Zezwól" }));
    expect(
      await screen.findByText(/Adres powrotu do aplikacji jest nieprawidłowy/),
    ).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
    expect(isSafeRedirect(BACK)).toBe(true);
    expect(isSafeRedirect("data:text/html,x")).toBe(false);
  });

  it("użytkownik bez organizacji może tylko odmówić", async () => {
    fetchMock.mockResolvedValue(json({ ...REQUEST, organizations: [] }));
    renderView(<ConsentView requestId={REQUEST_ID} defaultOrgId={null} navigate={navigate} />);
    expect(await screen.findByText(/Nie należysz do żadnej organizacji/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zezwól" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Odmów" })).toBeInTheDocument();
  });
});

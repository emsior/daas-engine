import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Schedule } from "@/lib/api/types";
import { renderWithProviders } from "@/test/utils";
import { ScheduleSettingsCard } from "./ScheduleSettings";

const DEFAULTS: Schedule = {
  reminder_enabled: false,
  reminder_weekday: 0,
  reminder_hour: 8,
  timezone: "Europe/Warsaw",
  reminder_skip_if_recent_upload: true,
  auto_run_on_upload: false,
  notify_on_finish: false,
  notify_webhook_kind: "slack",
  notify_webhook_set: false,
  notify_webhook_hint: null,
  next_reminder_at: null,
  last_reminder_at: null,
  updated_at: null,
};

/** Zapisany webhook Slacka: API zwraca tylko zamaskowany podpis, nigdy adres. */
const WITH_WEBHOOK: Schedule = {
  ...DEFAULTS,
  notify_webhook_set: true,
  notify_webhook_hint: "hooks.slack.com/…XyZ9",
  updated_at: "2026-10-12T10:00:00Z",
};

function putBody(fetchMock: { mock: { calls: unknown[][] } }, index = 1): Record<string, unknown> {
  const init = fetchMock.mock.calls[index]?.[1] as RequestInit | undefined;
  expect(init?.method).toBe("PUT");
  return JSON.parse(String(init?.body)) as Record<string, unknown>;
}

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

describe("ScheduleSettingsCard", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("pokazuje wartości domyślne i informację, że przypomnienie jest wyłączone", async () => {
    fetchMock.mockResolvedValue(json(DEFAULTS));
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);

    const reminder = await screen.findByRole("switch", { name: /Przypominaj e-mailem/ });
    expect(reminder).not.toBeChecked();
    expect(screen.getByRole("switch", { name: /Nie przypominaj/ })).toBeChecked();
    expect(
      screen.getByRole("switch", { name: /Uruchamiaj raport automatycznie/ }),
    ).not.toBeChecked();
    expect(screen.getByRole("switch", { name: /Wyślij e-mail/ })).not.toBeChecked();
    expect(screen.getByLabelText("Dzień tygodnia")).toHaveValue("0");
    expect(screen.getByLabelText("Godzina")).toHaveValue("8");
    expect(screen.getByLabelText(/Strefa czasowa/)).toHaveValue("Europe/Warsaw");
    expect(screen.getByText("Przypomnienie jest wyłączone.")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/\/orgs\/ORG1\/schedule$/);
  });

  it("zapisuje pełny stan przez PUT, ogłasza sukces i pokazuje następne przypomnienie w strefie", async () => {
    const user = userEvent.setup();
    const saved: Schedule = {
      ...DEFAULTS,
      reminder_enabled: true,
      reminder_weekday: 1,
      reminder_hour: 9,
      notify_on_finish: true,
      next_reminder_at: "2026-10-13T07:00:00Z",
      updated_at: "2026-10-12T10:00:00Z",
    };
    fetchMock.mockResolvedValueOnce(json(DEFAULTS)).mockResolvedValueOnce(json(saved));
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);

    await user.click(await screen.findByRole("switch", { name: /Przypominaj e-mailem/ }));
    await user.selectOptions(screen.getByLabelText("Dzień tygodnia"), "1");
    await user.selectOptions(screen.getByLabelText("Godzina"), "9");
    await user.click(screen.getByRole("switch", { name: /Wyślij e-mail/ }));
    await user.click(screen.getByRole("button", { name: "Zapisz harmonogram" }));

    expect(
      await screen.findByText("Harmonogram zapisany.", { selector: "[role=status]" }),
    ).toBeVisible();
    const [url, init] = fetchMock.mock.calls[1] ?? [];
    expect(String(url)).toMatch(/\/orgs\/ORG1\/schedule$/);
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({
      reminder_enabled: true,
      reminder_weekday: 1,
      reminder_hour: 9,
      timezone: "Europe/Warsaw",
      reminder_skip_if_recent_upload: true,
      auto_run_on_upload: false,
      notify_on_finish: true,
      notify_webhook_kind: "slack",
    });
    expect(
      screen.getByText("Następne przypomnienie: 13.10.2026 09:00 (Europe/Warsaw)"),
    ).toBeInTheDocument();
  });

  it("zapisany webhook czatu: pokazuje zamaskowany adres, pole adresu jest puste", async () => {
    fetchMock.mockResolvedValue(json(WITH_WEBHOOK));
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);

    const url = await screen.findByLabelText("Adres webhooka");
    expect(url).toHaveValue("");
    expect(url).toHaveAttribute("type", "url");
    expect(screen.getByTestId("schedule-webhook-current")).toHaveTextContent(
      "Ustawiono: hooks.slack.com/…XyZ9",
    );
    expect(url).toHaveAccessibleDescription(/Ustawiono: hooks\.slack\.com\/…XyZ9/);
    expect(screen.getByLabelText("Rodzaj czatu")).toHaveValue("slack");
    expect(screen.getByRole("button", { name: "Usuń adres" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/https:\/\/hooks/);
  });

  it("zapis bez dotykania adresu nie wysyła notify_webhook_url (adres zostaje)", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(json(WITH_WEBHOOK)).mockResolvedValueOnce(json(WITH_WEBHOOK));
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);

    await user.click(await screen.findByRole("switch", { name: /Wyślij e-mail/ }));
    await user.click(screen.getByRole("button", { name: "Zapisz harmonogram" }));

    await screen.findByText("Harmonogram zapisany.", { selector: "[role=status]" });
    const body = putBody(fetchMock);
    expect(body).not.toHaveProperty("notify_webhook_url");
    expect(body).toMatchObject({ notify_on_finish: true, notify_webhook_kind: "slack" });
  });

  it("nowy adres i rodzaj czatu są wysyłane, a po zapisie pole adresu jest czyszczone", async () => {
    const user = userEvent.setup();
    const saved: Schedule = {
      ...DEFAULTS,
      notify_webhook_kind: "teams",
      notify_webhook_set: true,
      notify_webhook_hint: "prod-00.westeurope.logic.azure.com/…voke",
    };
    fetchMock.mockResolvedValueOnce(json(DEFAULTS)).mockResolvedValueOnce(json(saved));
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);

    expect(await screen.findByTestId("schedule-webhook-current")).toHaveTextContent(
      "Brak zapisanego adresu",
    );
    expect(screen.queryByRole("button", { name: "Usuń adres" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Rodzaj czatu"), "teams");
    expect(screen.getByText(/aplikację Workflows/)).toBeInTheDocument(); // pomoc dla wybranego czatu
    const address =
      "https://prod-00.westeurope.logic.azure.com/workflows/1/triggers/manual/paths/invoke";
    await user.type(screen.getByLabelText("Adres webhooka"), `  ${address}  `);
    await user.click(screen.getByRole("button", { name: "Zapisz harmonogram" }));

    await screen.findByText("Harmonogram zapisany.", { selector: "[role=status]" });
    expect(putBody(fetchMock)).toMatchObject({
      notify_webhook_kind: "teams",
      notify_webhook_url: address,
    });
    expect(screen.getByLabelText("Adres webhooka")).toHaveValue("");
    expect(screen.getByTestId("schedule-webhook-current")).toHaveTextContent(
      "Ustawiono: prod-00.westeurope.logic.azure.com/…voke",
    );
  });

  it("„Usuń adres” wysyła null, a „Cofnij usunięcie” zostawia adres", async () => {
    const user = userEvent.setup();
    fetchMock
      .mockResolvedValueOnce(json(WITH_WEBHOOK))
      .mockResolvedValueOnce(
        json({ ...WITH_WEBHOOK, notify_webhook_set: false, notify_webhook_hint: null }),
      );
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);

    await user.click(await screen.findByRole("button", { name: "Usuń adres" }));
    expect(screen.getByTestId("schedule-webhook-current")).toHaveTextContent(
      "Adres zostanie usunięty po zapisaniu harmonogramu.",
    );
    await user.click(screen.getByRole("button", { name: "Cofnij usunięcie" }));
    expect(screen.getByTestId("schedule-webhook-current")).toHaveTextContent("Ustawiono:");
    await user.click(screen.getByRole("button", { name: "Usuń adres" }));
    await user.click(screen.getByRole("button", { name: "Zapisz harmonogram" }));

    await screen.findByText("Harmonogram zapisany.", { selector: "[role=status]" });
    expect(putBody(fetchMock).notify_webhook_url).toBeNull();
    expect(screen.getByTestId("schedule-webhook-current")).toHaveTextContent(
      "Brak zapisanego adresu",
    );
    expect(screen.queryByRole("button", { name: "Usuń adres" })).not.toBeInTheDocument();
  });

  it("adres bez https:// jest odrzucany w kokpicie bez wysyłki", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(json(DEFAULTS));
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);

    await user.type(await screen.findByLabelText("Adres webhooka"), "http://hooks.example.test/x");
    await user.click(screen.getByRole("button", { name: "Zapisz harmonogram" }));

    const message = /Wklej pełny adres zaczynający się od https:\/\//;
    expect(await screen.findByRole("link", { name: message })).toHaveAttribute(
      "href",
      "#schedule-webhook-url",
    );
    expect(screen.getByLabelText("Adres webhooka")).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("błąd 422 adresu z API pokazuje komunikat przy polu adresu", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(json(DEFAULTS)).mockResolvedValueOnce(
      json(
        {
          status: 422,
          code: "validation_error",
          title: "Unprocessable Entity",
          errors: [
            {
              loc: ["body", "notify_webhook_url"],
              msg: "the chat webhook URL must point to a public address",
              type: "private_address",
            },
          ],
        },
        422,
      ),
    );
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);

    const url = await screen.findByLabelText("Adres webhooka");
    await user.type(url, "https://intranet.example.test/hook");
    await user.click(screen.getByRole("button", { name: "Zapisz harmonogram" }));

    await waitFor(() => expect(url).toHaveAttribute("aria-invalid", "true"));
    const message = screen.getAllByText(/Adresy w sieci lokalnej nie są dozwolone/);
    expect(message).toHaveLength(2); // pole + podsumowanie
    expect(url).toHaveValue("https://intranet.example.test/hook"); // wpisany adres zostaje do poprawy
    expect(screen.queryByText("Harmonogram zapisany.")).not.toBeInTheDocument();
  });

  it("błąd 422 z API pokazuje komunikat przy polu strefy i podsumowanie błędów", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(json(DEFAULTS)).mockResolvedValueOnce(
      json(
        {
          status: 422,
          code: "validation_error",
          title: "Unprocessable Entity",
          errors: [{ loc: ["body", "timezone"], msg: "Value error, unknown IANA time zone" }],
        },
        422,
      ),
    );
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);

    const tz = await screen.findByLabelText(/Strefa czasowa/);
    await user.clear(tz);
    await user.type(tz, "Mars/Olympus");
    await user.click(screen.getByRole("button", { name: "Zapisz harmonogram" }));

    await waitFor(() => expect(tz).toHaveAttribute("aria-invalid", "true"));
    const message = "Nieznana strefa czasowa. Wpisz nazwę IANA, np. Europe/Warsaw.";
    expect(screen.getAllByText(message)).toHaveLength(2); // pole + podsumowanie
    expect(screen.getByRole("link", { name: message })).toHaveAttribute(
      "href",
      "#schedule-timezone",
    );
    expect(screen.queryByText("Harmonogram zapisany.")).not.toBeInTheDocument();
  });

  it("puste pole strefy jest odrzucane bez wysyłki", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(json(DEFAULTS));
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);
    await user.clear(await screen.findByLabelText(/Strefa czasowa/));
    await user.click(screen.getByRole("button", { name: "Zapisz harmonogram" }));
    expect(await screen.findByRole("link", { name: /Nieznana strefa czasowa/ })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("inny błąd zapisu (403) pokazuje komunikat ogólny", async () => {
    const user = userEvent.setup();
    fetchMock
      .mockResolvedValueOnce(json(DEFAULTS))
      .mockResolvedValueOnce(json({ status: 403, code: "insufficient_scope" }, 403));
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);
    await user.click(await screen.findByRole("button", { name: "Zapisz harmonogram" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("bez uprawnień (member) pokazuje odczyt bez formularza", async () => {
    fetchMock.mockResolvedValue(
      json({
        ...DEFAULTS,
        reminder_enabled: true,
        notify_on_finish: true,
        notify_webhook_kind: "teams",
        notify_webhook_set: true,
        notify_webhook_hint: "prod-00.westeurope.logic.azure.com/…voke",
        next_reminder_at: "2026-10-19T06:00:00Z",
        updated_at: "2026-10-12T10:00:00Z",
      }),
    );
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage={false} />);

    expect(await screen.findByText(/zmienia właściciel albo administrator/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zapisz harmonogram" })).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.getByText("włączone: poniedziałek, 08:00 (Europe/Warsaw)")).toBeInTheDocument();
    expect(
      screen.getByText("Microsoft Teams: prod-00.westeurope.logic.azure.com/…voke"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Adres webhooka")).not.toBeInTheDocument();
    expect(
      screen.getByText("Następne przypomnienie: 19.10.2026 08:00 (Europe/Warsaw)"),
    ).toBeInTheDocument();
  });

  it("błąd pobrania pokazuje stan błędu", async () => {
    fetchMock.mockResolvedValue(json({ status: 500, code: "internal_error" }, 500));
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);
    expect(await screen.findByText("Nie udało się pobrać harmonogramu")).toBeInTheDocument();
  });
});

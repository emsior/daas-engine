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
  next_reminder_at: null,
  last_reminder_at: null,
  updated_at: null,
};

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
    });
    expect(
      screen.getByText("Następne przypomnienie: 13.10.2026 09:00 (Europe/Warsaw)"),
    ).toBeInTheDocument();
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
      screen.getByText("Następne przypomnienie: 19.10.2026 08:00 (Europe/Warsaw)"),
    ).toBeInTheDocument();
  });

  it("błąd pobrania pokazuje stan błędu", async () => {
    fetchMock.mockResolvedValue(json({ status: 500, code: "internal_error" }, 500));
    renderCard(<ScheduleSettingsCard orgId="ORG1" canManage />);
    expect(await screen.findByText("Nie udało się pobrać harmonogramu")).toBeInTheDocument();
  });
});

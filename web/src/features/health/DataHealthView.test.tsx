import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DataHealth, DataHealthSummary, HealthIssue, WeekHealth } from "@/lib/api/types";
import { renderWithProviders } from "@/test/utils";
import { DataHealthView } from "./DataHealthView";

const DAY_MS = 86_400_000;
const CURRENT_MONDAY = Date.UTC(2026, 9, 12);

/** `count` tygodni od najstarszego do bieżącego (12.10.2026); `reports(i)` = raporty i-tego tygodnia. */
function makeWeeks(count: number, reports: (index: number) => number = () => 1): WeekHealth[] {
  return Array.from({ length: count }, (_, index) => {
    const back = count - 1 - index;
    return {
      week_start: new Date(CURRENT_MONDAY - back * 7 * DAY_MS).toISOString().slice(0, 10),
      partial: back === 0,
      uploads: 1,
      upload_bytes: 100,
      runs_succeeded: 1,
      runs_failed: 0,
      reports: reports(index),
      avg_run_seconds: 42,
      last_report_at: null,
    };
  });
}

const SUMMARY: DataHealthSummary = {
  last_upload_at: "2026-10-13T08:00:00Z",
  last_success_at: "2026-10-13T08:02:00Z",
  last_failure_at: null,
  last_error_code: null,
  streak_weeks: 11,
  reminder_enabled: true,
  next_reminder_at: "2026-10-19T06:00:00Z",
  auto_run_on_upload: true,
  notify_on_finish: false,
  plan: "free",
  runs_used: 4,
  runs_limit: 20,
  retention_days: 90,
};

function health(overrides: Partial<DataHealth> = {}): DataHealth {
  return {
    generated_at: "2026-10-14T10:00:00Z",
    timezone: "Europe/Warsaw",
    weeks: makeWeeks(12, (index) => (index === 10 ? 4 : index % 2)),
    summary: SUMMARY,
    issues: [],
    ...overrides,
  };
}

const INFO_ONLY: HealthIssue[] = [
  { code: "reminder_off", severity: "info", since: null, link: "/settings" },
  { code: "auto_run_off", severity: "info", since: null, link: "/settings" },
];

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

describe("DataHealthView", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("czerwony werdykt przy nieudanym ostatnim uruchomieniu, z linkiem do runu i kodem błędu", async () => {
    fetchMock.mockResolvedValue(
      json(
        health({
          summary: {
            ...SUMMARY,
            last_failure_at: "2026-10-13T09:00:00Z",
            last_error_code: "RUN_FAILED",
          },
          issues: [
            {
              code: "last_run_failed",
              severity: "error",
              since: "2026-10-13T09:00:00Z",
              link: "/runs/RUN1",
            },
            { code: "plan_limit_near", severity: "warning", since: null, link: "/settings" },
            ...INFO_ONLY,
          ],
        }),
      ),
    );
    renderView(<DataHealthView orgId="ORG1" />);

    const heading = await screen.findByRole("heading", {
      level: 2,
      name: "Raport w przyszły poniedziałek: zagrożony",
    });
    expect(heading.querySelector(".badge-error")).not.toBeNull();
    const failed = screen.getByRole("link", { name: "Ostatnie uruchomienie zakończyło się błędem" });
    expect(failed).toHaveAttribute("href", "/runs/RUN1");
    const item = failed.closest("li");
    expect(item).not.toBeNull();
    expect(within(item as HTMLElement).getByText("Błąd")).toBeInTheDocument();
    expect(within(item as HTMLElement).getByText("kod błędu: RUN_FAILED")).toBeInTheDocument();
    expect(within(item as HTMLElement).getByText("od 13.10.2026")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Limit uruchomień w planie się kończy" })).toHaveAttribute(
      "href",
      "/settings",
    );
    // kolejność z API: najpierw błąd
    const links = within(failed.closest("ul") as HTMLElement).getAllByRole("link");
    expect(links).toHaveLength(4);
    expect(links[0]).toBe(failed);
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/\/orgs\/ORG1\/data-health\?weeks=12$/);
  });

  it("zielony werdykt, gdy zostały same wskazówki; wskaźniki z podsumowania", async () => {
    fetchMock.mockResolvedValue(json(health({ issues: INFO_ONLY })));
    renderView(<DataHealthView orgId="ORG1" />);

    const heading = await screen.findByRole("heading", {
      level: 2,
      name: "Raport w przyszły poniedziałek: bezpieczny",
    });
    expect(heading.querySelector(".badge-ok")).not.toBeNull();
    expect(screen.getByRole("link", { name: "Przypomnienie o eksporcie jest wyłączone" })).toHaveAttribute(
      "href",
      "/settings",
    );
    expect(screen.getAllByText("Wskazówka")).toHaveLength(2);
    expect(screen.getByText("Tygodnie z raportem z rzędu").nextElementSibling).toHaveTextContent("11");
    expect(screen.getByText("Ostatni plik").nextElementSibling).toHaveTextContent("13.10.2026");
    expect(screen.getByText("Uruchomienia w tym miesiącu").nextElementSibling).toHaveTextContent(
      "4 z 20",
    );
    expect(screen.getByText("Następne przypomnienie").nextElementSibling).toHaveTextContent(
      "19.10.2026",
    );
    expect(screen.getByText("08:00 (Europe/Warsaw)")).toBeInTheDocument();
  });

  it("żółty werdykt przy samych ostrzeżeniach", async () => {
    fetchMock.mockResolvedValue(
      json(
        health({
          issues: [
            { code: "stale_upload", severity: "warning", since: "2026-10-01T08:00:00Z", link: "/upload" },
          ],
        }),
      ),
    );
    renderView(<DataHealthView orgId="ORG1" />);
    const heading = await screen.findByRole("heading", {
      level: 2,
      name: "Raport w przyszły poniedziałek: zagrożony",
    });
    expect(heading.querySelector(".badge-warn")).not.toBeNull();
    expect(screen.getByText("od 01.10.2026")).toBeInTheDocument();
  });

  it("pusta organizacja: brak danych i wezwanie do wgrania pliku, bez tabeli", async () => {
    fetchMock.mockResolvedValue(
      json(
        health({
          weeks: makeWeeks(12, () => 0),
          summary: {
            ...SUMMARY,
            last_upload_at: null,
            last_success_at: null,
            streak_weeks: 0,
            reminder_enabled: false,
            next_reminder_at: null,
            auto_run_on_upload: false,
            runs_used: 0,
          },
          issues: [
            { code: "no_uploads_yet", severity: "info", since: null, link: "/upload" },
            ...INFO_ONLY,
          ],
        }),
      ),
    );
    renderView(<DataHealthView orgId="ORG1" />);

    await screen.findByRole("heading", { level: 2, name: "Raport w przyszły poniedziałek: brak danych" });
    expect(screen.getByRole("link", { name: "Wgraj pierwszy plik" })).toHaveAttribute("href", "/upload");
    expect(screen.getByRole("link", { name: "Nie wgrano jeszcze żadnego pliku" })).toHaveAttribute(
      "href",
      "/upload",
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it("tabela ma 12 wierszy od najnowszego, liczby w tekście i dekoracyjne słupki", async () => {
    fetchMock.mockResolvedValue(json(health({ issues: INFO_ONLY })));
    renderView(<DataHealthView orgId="ORG1" />);

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(13); // nagłówek + 12 tygodni
    const [header, current, previous] = rows;
    expect(within(header as HTMLElement).getAllByRole("columnheader").map((c) => c.textContent)).toEqual([
      "Tydzień",
      "Pliki",
      "Raporty",
      "Błędy",
      "Śr. czas",
    ]);
    expect(current).toHaveTextContent("12–18.10.2026");
    expect(current).toHaveTextContent("tydz. 42");
    expect(within(current as HTMLElement).getByText("w toku")).toBeInTheDocument();
    expect(previous).toHaveTextContent("05–11.10.2026");
    expect(within(previous as HTMLElement).queryByText("w toku")).not.toBeInTheDocument();
    expect(within(previous as HTMLElement).getByText("42 s")).toBeInTheDocument(); // NBSP po normalizacji tekstu

    const bars = within(table).getAllByTestId("health-bar");
    expect(bars).toHaveLength(12);
    for (const bar of bars) expect(bar).toHaveAttribute("aria-hidden", "true");
    // wiersz k (od 1) to tydzień o indeksie 12 - k: 4 raporty (maksimum) → pełny słupek, 1 → 25 %, 0 → pusty
    const reportsCell = (row: HTMLElement | undefined) =>
      within(row as HTMLElement).getAllByRole("cell")[2] as HTMLElement;
    const fill = (row: HTMLElement | undefined) =>
      within(reportsCell(row)).getByTestId("health-bar").firstElementChild;
    expect(reportsCell(previous)).toHaveTextContent(/^4$/);
    expect(fill(previous)).toHaveAttribute("data-percent", "100");
    expect(fill(previous)).toHaveClass("w-full");
    expect(reportsCell(rows[3])).toHaveTextContent(/^1$/);
    expect(fill(rows[3])).toHaveAttribute("data-percent", "25");
    expect(fill(rows[3])).toHaveClass("w-[25%]");
    expect(reportsCell(rows[4])).toHaveTextContent(/^0$/);
    expect(fill(rows[4])).toHaveAttribute("data-percent", "0");
    expect(fill(rows[4])).toHaveClass("w-0");
    expect(table.querySelector("[style]")).toBeNull(); // CSP: bez stylów inline
  });

  it("przełącznik 26 tygodni woła API z weeks=26 i pokazuje 26 wierszy", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input) => {
      const weeks = Number(new URL(String(input), "https://cockpit.example.test").searchParams.get("weeks"));
      return json(health({ weeks: makeWeeks(weeks), issues: INFO_ONLY }));
    });
    renderView(<DataHealthView orgId="ORG1" />);

    const twelve = await screen.findByRole("radio", { name: "12 tygodni" });
    expect(twelve).toBeChecked();
    expect(screen.getByRole("group", { name: "Zakres tabeli" })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "26 tygodni" }));

    await waitFor(() =>
      expect(String(fetchMock.mock.calls.at(-1)?.[0])).toMatch(/\/orgs\/ORG1\/data-health\?weeks=26$/),
    );
    expect(await screen.findByRole("heading", { level: 2, name: "Ostatnie 26 tygodni" })).toBeVisible();
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(27);
    expect(screen.getByRole("radio", { name: "26 tygodni" })).toBeChecked();
    await user.click(screen.getByRole("radio", { name: "8 tygodni" }));
    await waitFor(() =>
      expect(String(fetchMock.mock.calls.at(-1)?.[0])).toMatch(/data-health\?weeks=8$/),
    );
  });

  it("błąd pobrania pokazuje stan błędu z ponowieniem", async () => {
    fetchMock.mockResolvedValue(json({ status: 500, code: "internal_error" }, 500));
    renderView(<DataHealthView orgId="ORG1" />);
    expect(await screen.findByText("Nie udało się pobrać zdrowia danych")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Spróbuj ponownie" })).toBeInTheDocument();
  });
});

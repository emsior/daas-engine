import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Run } from "./api/types";
import { renderMarkdown } from "./markdown";
import { runKpis } from "./run-kpis";
import { checkStatusToBadge, runStatusToBadge } from "./status";
import { kindOfFile, validateUploadFile } from "./upload-validation";

const run = (over: Partial<Run>): Run => ({
  run_id: "r",
  pipeline: "ecommerce_demo",
  status: "SUCCESS",
  created_at: "2026-10-09T08:00:00Z",
  report_sha256: {},
  metrics: {},
  ...over,
});

describe("run-kpis", () => {
  it("zwraca przychód, zysk i zamówienia dla raportu sprzedaży", () => {
    const kpis = runKpis(
      run({ metrics: { revenue_total: 7255.95, profit_total: 2310.4, orders_total: 30 } }),
    );
    expect(kpis.map((k) => k.value)).toEqual(["7 255,95 zł", "2 310,40 zł", "30"]);
  });

  it("zwraca liczby kontroli dla raportu artefaktów i myślnik przy braku danych", () => {
    const kpis = runKpis(run({ pipeline: "uksc_evidence", metrics: { passed: 18, failed: 3 } }));
    expect(kpis.map((k) => k.value)).toEqual(["18", "3", "—"]);
  });
});

describe("status", () => {
  it("mapuje statusy runu i kontroli na odznaki", () => {
    expect(runStatusToBadge("SUCCESS")).toBe("ok");
    expect(runStatusToBadge("PARTIAL")).toBe("warn");
    expect(runStatusToBadge("FAILED")).toBe("error");
    expect(runStatusToBadge("???")).toBe("unknown");
    expect(checkStatusToBadge("PASS")).toBe("ok");
    expect(checkStatusToBadge("MANUAL")).toBe("pending");
    expect(checkStatusToBadge("NA_NO_ADMIN")).toBe("unknown");
  });
});

describe("upload-validation", () => {
  it("rozpoznaje rodzaj pliku po rozszerzeniu", () => {
    expect(kindOfFile("A.CSV")).toBe("csv");
    expect(kindOfFile("a.json")).toBe("package");
    expect(kindOfFile("a.xlsm")).toBeNull();
    expect(validateUploadFile({ name: "a.csv", size: 5 })).toBeNull();
  });
});

describe("renderMarkdown", () => {
  it("renderuje nagłówki, listy i pogrubienie jako JSX", () => {
    const { container } = render(
      <>{renderMarkdown("## Tytuł\n\nAkapit **ważny**.\n\n- a\n- b\n")}</>,
    );
    expect(container.querySelector("h2")?.textContent).toBe("Tytuł");
    expect(container.querySelector("strong")?.textContent).toBe("ważny");
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("nie wykonuje HTML z treści (XSS)", () => {
    const { container } = render(
      <>{renderMarkdown("<img src=x onerror=alert(1)> <script>alert(1)</script>")}</>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<script>");
  });
});

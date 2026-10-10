import { describe, expect, it } from "vitest";
import type { UploadPreview } from "@/lib/api/types";
import { initialState, stepOf, uploadReducer } from "./uploadReducer";

const preview: UploadPreview = {
  upload_id: "a".repeat(32),
  filename: "x.csv",
  rows: 30,
  columns: [],
  mapping: {},
  unmapped_columns: [],
  generated: [],
  warnings: [],
  confidence: 1,
  ready: true,
};

describe("uploadReducer", () => {
  it("odrzucenie pliku czyści wybór, zapisuje błąd i zwiększa licznik prób (fokus podsumowania)", () => {
    const state = uploadReducer(initialState, {
      type: "reject",
      error: { code: "type", extension: "png" },
    });
    expect(state.file).toBeNull();
    expect(state.error).toEqual({ code: "type", extension: "png" });
    expect(state.attempt).toBe(1);
  });

  it("błąd serwera przy uploadzie wraca do wyboru i zachowuje plik", () => {
    const file = new File(["a"], "a.csv");
    let state = uploadReducer(initialState, { type: "select", file });
    state = uploadReducer(state, { type: "upload_start" });
    state = uploadReducer(state, { type: "upload_fail", error: { code: "server" } });
    expect(state.phase).toBe("choose");
    expect(state.file).toBe(file);
  });

  it("przechodzi choose -> uploading -> preview -> starting -> running", () => {
    let state = uploadReducer(initialState, { type: "upload_start" });
    expect(stepOf(state.phase)).toBe(0);
    state = uploadReducer(state, { type: "upload_ok", preview });
    expect(state.phase).toBe("preview");
    state = uploadReducer(state, { type: "job_start" });
    state = uploadReducer(state, { type: "job_created", jobId: "j1", status: "queued" });
    expect(state.phase).toBe("running");
    expect(stepOf(state.phase)).toBe(2);
  });

  it("ogranicza postęp do 0-100 i zachowuje wiersze", () => {
    let state = uploadReducer(initialState, {
      type: "progress",
      percent: 140,
      rowsDone: 10,
      rowsTotal: 20,
    });
    expect(state.progress.percent).toBe(100);
    state = uploadReducer(state, { type: "progress", percent: 50 });
    expect(state.progress.rowsDone).toBe(10);
  });

  it("zakończenie bez run_id czeka na odpytywanie, z run_id kończy sukcesem", () => {
    let state = uploadReducer(
      { ...initialState, phase: "running" },
      { type: "finished", status: "succeeded", runId: null, errorCode: null },
    );
    expect(state.phase).toBe("running");
    expect(state.live).toBe("polling");
    state = uploadReducer(state, {
      type: "finished",
      status: "succeeded",
      runId: "r1",
      errorCode: null,
    });
    expect(state.phase).toBe("done");
    expect(state.runId).toBe("r1");
  });

  it("obsługuje błąd i anulowanie zadania", () => {
    const failed = uploadReducer(
      { ...initialState, phase: "running" },
      { type: "finished", status: "failed", runId: null, errorCode: "parser_error" },
    );
    expect(failed.phase).toBe("failed");
    expect(failed.failCode).toBe("parser_error");
    const cancelled = uploadReducer(
      { ...initialState, phase: "running" },
      { type: "finished", status: "cancelled", runId: null, errorCode: null },
    );
    expect(cancelled.phase).toBe("cancelled");
  });
});

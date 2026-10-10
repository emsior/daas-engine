import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { connectJobEvents, type EventSourceLike } from "./sse";

class FakeSource implements EventSourceLike {
  readyState = 0;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  private listeners = new Map<string, (event: MessageEvent<string>) => void>();
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.set(type, listener);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  emit(type: string, data: unknown) {
    this.listeners.get(type)?.({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
  fail(readyState: number) {
    this.readyState = readyState;
    this.onerror?.(new Event("error"));
  }
}

describe("connectJobEvents", () => {
  let sources: FakeSource[];
  const factory = () => {
    const source = new FakeSource();
    sources.push(source);
    return source;
  };

  beforeEach(() => {
    sources = [];
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it("przekazuje zdarzenia progress i done oraz zamyka połączenie po done", () => {
    const onProgress = vi.fn();
    const onDone = vi.fn();
    connectJobEvents("/x", { onProgress, onDone }, { factory });
    sources[0]?.emit("progress", { progress: 40, rows_done: 400, rows_total: 1000 });
    sources[0]?.emit("done", { status: "succeeded", run_id: "r1" });
    expect(onProgress).toHaveBeenCalledWith({ progress: 40, rows_done: 400, rows_total: 1000 });
    expect(onDone).toHaveBeenCalledWith({ status: "succeeded", run_id: "r1" });
    expect(sources[0]?.closed).toBe(true);
  });

  it("ignoruje zdarzenia o niepoprawnym kształcie", () => {
    const onProgress = vi.fn();
    connectJobEvents("/x", { onProgress }, { factory });
    sources[0]?.emit("progress", { progress: "dużo" });
    expect(onProgress).not.toHaveBeenCalled();
  });

  it("ponawia połączenie z opóźnieniem, gdy zostało zamknięte", () => {
    const onReconnecting = vi.fn();
    connectJobEvents("/x", { onReconnecting }, { factory, baseDelayMs: 1000 });
    sources[0]?.fail(2);
    expect(onReconnecting).toHaveBeenCalledWith(1);
    expect(sources).toHaveLength(1);
    vi.advanceTimersByTime(1000);
    expect(sources).toHaveLength(2);
  });

  it("po przekroczeniu limitu prób wywołuje onGiveUp", () => {
    const onGiveUp = vi.fn();
    connectJobEvents("/x", { onGiveUp }, { factory, maxRetries: 2, baseDelayMs: 10 });
    for (let i = 0; i < 3; i += 1) {
      sources[sources.length - 1]?.fail(2);
      vi.advanceTimersByTime(100);
    }
    expect(onGiveUp).toHaveBeenCalledOnce();
  });

  it("funkcja zwrotna zamyka źródło i kasuje ponowienia", () => {
    const stop = connectJobEvents("/x", {}, { factory });
    stop();
    expect(sources[0]?.closed).toBe(true);
  });
});

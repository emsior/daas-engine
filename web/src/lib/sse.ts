/**
 * EventSource z ponawianiem dla zdarzeń zadania (`status`, `progress`, `log`, `done`).
 * - Gdy przeglądarka sama wznawia połączenie (readyState CONNECTING), czekamy.
 * - Gdy połączenie jest zamknięte (CLOSED, np. po 502 z proxy), otwieramy nowe z wykładniczym opóźnieniem.
 * - Po `maxRetries` kolejnych błędach bez udanego otwarcia wołamy `onGiveUp` (UI przechodzi na odpytywanie).
 */
import { z } from "zod";
import "./zod-config";
import type { JobDoneEvent, JobLogEvent, JobProgressEvent, JobStatusEvent } from "./api/types";

const jobStatus = z.enum(["queued", "running", "succeeded", "failed", "cancelled"]);

const statusSchema = z.object({ status: jobStatus, progress: z.number().optional() });
const progressSchema = z.object({
  progress: z.number(),
  rows_done: z.number().optional(),
  rows_total: z.number().optional(),
  message: z.string().optional(),
});
const logSchema = z.object({ message: z.string() });
const doneSchema = z.object({
  status: jobStatus,
  run_id: z.string().nullish(),
  error_code: z.string().nullish(),
});

export interface JobEventHandlers {
  onOpen?: () => void;
  onStatus?: (event: JobStatusEvent) => void;
  onProgress?: (event: JobProgressEvent) => void;
  onLog?: (event: JobLogEvent) => void;
  onDone?: (event: JobDoneEvent) => void;
  onReconnecting?: (attempt: number) => void;
  onGiveUp?: () => void;
}

/** Minimalny interfejs EventSource potrzebny w kodzie (ułatwia testy). */
export interface EventSourceLike {
  readyState: number;
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void;
  close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

export interface ConnectOptions {
  maxRetries?: number;
  /** Limit prób, gdy strumień ani razu się nie otworzył (np. 429 `sse_limit`: EventSource nie ujawnia statusu). */
  maxRetriesNeverOpened?: number;
  baseDelayMs?: number;
  factory?: EventSourceFactory;
}

const CLOSED = 2;

function defaultFactory(url: string): EventSourceLike {
  return new EventSource(url, { withCredentials: true }) as unknown as EventSourceLike;
}

function parse<T>(schema: z.ZodType<T>, raw: string): T | null {
  try {
    const result = schema.safeParse(JSON.parse(raw));
    return result.success ? result.data : null;
  } catch {
    return null;
  }
}

/** Zwraca funkcję zamykającą połączenie i kasującą zaplanowane ponowienia. */
export function connectJobEvents(
  url: string,
  handlers: JobEventHandlers,
  options: ConnectOptions = {},
): () => void {
  const maxRetries = options.maxRetries ?? 5;
  const neverOpenedRetries = options.maxRetriesNeverOpened ?? 2;
  let opened = false;
  const baseDelay = options.baseDelayMs ?? 1000;
  const factory = options.factory ?? defaultFactory;

  let source: EventSourceLike | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let failures = 0;
  let stopped = false;

  const stop = () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    timer = null;
    source?.close();
    source = null;
  };

  const open = () => {
    if (stopped) return;
    const es = factory(url);
    source = es;

    es.onopen = () => {
      opened = true;
      failures = 0;
      handlers.onOpen?.();
    };

    es.addEventListener("status", (event) => {
      const data = parse(statusSchema, event.data);
      if (data) handlers.onStatus?.(data);
    });
    es.addEventListener("progress", (event) => {
      const data = parse(progressSchema, event.data);
      if (data) handlers.onProgress?.(data);
    });
    es.addEventListener("log", (event) => {
      const data = parse(logSchema, event.data);
      if (data) handlers.onLog?.(data);
    });
    es.addEventListener("done", (event) => {
      const data = parse(doneSchema, event.data);
      stop();
      if (data) handlers.onDone?.(data);
    });

    es.onerror = () => {
      if (stopped) return;
      failures += 1;
      if (failures > (opened ? maxRetries : Math.min(maxRetries, neverOpenedRetries))) {
        stop();
        handlers.onGiveUp?.();
        return;
      }
      handlers.onReconnecting?.(failures);
      if (es.readyState === CLOSED) {
        es.close();
        const delay = Math.min(baseDelay * 2 ** (failures - 1), 15000);
        timer = setTimeout(open, delay);
      }
    };
  };

  open();
  return stop;
}

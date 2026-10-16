import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import { classifyUploadFailure } from "./uploadErrors";

const problem = (status: number, code: string | null) =>
  new ApiError({ status, title: "x", code, requestId: "req-1" });

describe("classifyUploadFailure (kody odmowy backendu -> wzorce komunikatów)", () => {
  it.each([
    [413, "file_too_large", "size"],
    [413, "quota_exceeded", "quota"],
    [400, "file_empty", "empty"],
    [415, "extension_not_allowed", "type"],
    [422, "schema_mismatch", "columnsServer"],
    [422, "header_missing", "columnsServer"],
    [422, "decode_failed", "decode"],
    [422, "parser_error", "decode"],
    [422, "too_many_records", "records"],
    [422, "xlsx_multiple_sheets", "xlsx"],
    [422, "xlsx_macros_not_allowed", "xlsx"],
    [429, "rate_limited", "server"],
    [500, "internal_error", "server"],
  ] as const)("%i %s -> %s", (status, code, expected) => {
    expect(classifyUploadFailure(problem(status, code), "csv").code).toBe(expected);
  });

  it("paczka JSON z błędem schematu dostaje komunikat o schemacie paczki", () => {
    expect(classifyUploadFailure(problem(422, "schema_mismatch"), "package").code).toBe("package");
  });

  it("limit rozmiaru zależy od rodzaju pliku (paczka 5 MiB, tabela 10 MiB)", () => {
    const table = classifyUploadFailure(problem(413, "file_too_large"), "csv");
    const pack = classifyUploadFailure(problem(413, "file_too_large"), "package");
    expect(table).toEqual({ code: "size", limitBytes: 10 * 1024 * 1024 });
    expect(pack).toEqual({ code: "size", limitBytes: 5 * 1024 * 1024 });
  });

  it("błąd serwera niesie identyfikator żądania, błąd nie-API tylko ogólny komunikat", () => {
    expect(classifyUploadFailure(problem(500, null), "csv")).toEqual({
      code: "server",
      requestId: "req-1",
    });
    expect(classifyUploadFailure(new Error("x"), "csv")).toEqual({
      code: "server",
      requestId: null,
    });
  });
});

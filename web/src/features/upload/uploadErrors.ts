import { isApiError } from "@/lib/api/client";
import { MAX_PACKAGE_UPLOAD_BYTES, MAX_TABLE_UPLOAD_BYTES } from "@/lib/env";
import type { UploadKind } from "@/lib/upload-validation";
import type { UploadError } from "./uploadReducer";

/**
 * Kody odmowy backendu (`ReasonCode` w `problem+json`, pole `code`) -> wzorce komunikatów uploadu:
 * typ, rozmiar, pusty plik, brak kolumn, kodowanie/separator. Reszta to ogólny błąd serwera z identyfikatorem żądania.
 */
export function classifyUploadFailure(error: unknown, kind: UploadKind | null): UploadError {
  if (!isApiError(error)) return { code: "server", requestId: null };
  const limitBytes = kind === "package" ? MAX_PACKAGE_UPLOAD_BYTES : MAX_TABLE_UPLOAD_BYTES;
  switch (error.code) {
    case "upload_required":
      return { code: "none" };
    case "file_too_large":
      return { code: "size", limitBytes };
    case "quota_exceeded":
      return { code: "quota" };
    case "file_empty":
      return { code: "empty" };
    case "extension_not_allowed":
    case "filename_illegal_chars":
    case "filename_too_long":
    case "empty_filename":
      return { code: "type", extension: "" };
    case "schema_mismatch":
    case "header_missing":
      return kind === "package" ? { code: "package" } : { code: "columnsServer" };
    case "decode_failed":
    case "parser_error":
      return { code: "decode" };
    case "too_many_records":
    case "field_too_large":
    case "xlsx_too_wide":
      return { code: "records" };
    default:
      break;
  }
  if (error.code?.startsWith("xlsx_")) return { code: "xlsx" };
  if (error.status === 413) return { code: "size", limitBytes };
  return { code: "server", requestId: error.requestId };
}

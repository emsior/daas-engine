import { MAX_PACKAGE_UPLOAD_BYTES, MAX_TABLE_UPLOAD_BYTES } from "./env";

export type UploadKind = "csv" | "xlsx" | "package";

export type UploadRejection =
  { code: "empty" } | { code: "type"; extension: string } | { code: "size"; limitBytes: number };

export const ACCEPTED_EXTENSIONS = ["csv", "xlsx", "json"] as const;

export function extensionOf(filename: string): string {
  const index = filename.lastIndexOf(".");
  return index === -1 ? "" : filename.slice(index + 1).toLowerCase();
}

export function kindOfFile(filename: string): UploadKind | null {
  switch (extensionOf(filename)) {
    case "csv":
      return "csv";
    case "xlsx":
      return "xlsx";
    case "json":
      return "package";
    default:
      return null;
  }
}

/**
 * Walidacja po stronie klienta (wygoda, nie zabezpieczenie): typ csv/xlsx/json,
 * rozmiar <= 10 MiB (paczka JSON <= 5 MiB), plik niepusty. `.xlsm` jest odrzucany jako typ.
 */
export function validateUploadFile(file: { name: string; size: number }): UploadRejection | null {
  const kind = kindOfFile(file.name);
  if (!kind) return { code: "type", extension: extensionOf(file.name) };
  if (file.size === 0) return { code: "empty" };
  const limit = kind === "package" ? MAX_PACKAGE_UPLOAD_BYTES : MAX_TABLE_UPLOAD_BYTES;
  if (file.size > limit) return { code: "size", limitBytes: limit };
  return null;
}

/** Domyślny pipeline dla rodzaju pliku (backend może zasugerować inny). */
export function defaultPipelineFor(kind: UploadKind): string {
  return kind === "package" ? "uksc_evidence" : "ecommerce_demo";
}

/**
 * SHA-256 liczony lokalnie (Web Crypto). Plik nigdy nie opuszcza przeglądarki.
 * `crypto.subtle` istnieje tylko w bezpiecznym kontekście (HTTPS lub localhost).
 */

export class InsecureContextError extends Error {
  constructor() {
    super("crypto.subtle is unavailable (insecure context)");
    this.name = "InsecureContextError";
  }
}

function toHex(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let out = "";
  for (const byte of bytes) {
    out += byte.toString(16).padStart(2, "0");
  }
  return out;
}

export async function sha256HexOfBuffer(data: ArrayBuffer | Uint8Array): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new InsecureContextError();
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  // kopia do zwykłego ArrayBuffer (typy BufferSource nie dopuszczają SharedArrayBuffer)
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return toHex(await subtle.digest("SHA-256", copy));
}

export async function sha256HexOfBlob(blob: Blob): Promise<string> {
  return sha256HexOfBuffer(await blob.arrayBuffer());
}

/** Dzieli skrót na grupy po `size` znaków (domyślnie 8 grup po 8 dla SHA-256). */
export function groupHash(hex: string, size = 8): string[] {
  const groups: string[] = [];
  for (let i = 0; i < hex.length; i += size) {
    groups.push(hex.slice(i, i + size));
  }
  return groups;
}

/** Usuwa spacje, prefiks `sha256:` i ujednolica wielkość liter. */
export function normalizeHash(input: string): string {
  return input
    .trim()
    .replace(/^sha-?256[:=]?\s*/i, "")
    .replace(/\s+/g, "")
    .toLowerCase();
}

export function isSha256Hex(value: string): boolean {
  return /^[0-9a-f]{64}$/.test(value);
}

export function hashesMatch(a: string, b: string): boolean {
  return normalizeHash(a) === normalizeHash(b);
}

/** Skrót do tabel: pierwsze 8 znaków + wielokropek. */
export function shortHash(hex: string, length = 8): string {
  return hex.length > length ? `${hex.slice(0, length)}…` : hex;
}

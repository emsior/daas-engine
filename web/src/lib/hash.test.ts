import { describe, expect, it } from "vitest";
import {
  groupHash,
  hashesMatch,
  isSha256Hex,
  normalizeHash,
  sha256HexOfBlob,
  sha256HexOfBuffer,
  shortHash,
} from "./hash";

const ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
const EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

describe("hash", () => {
  it("liczy znany wektor SHA-256 dla 'abc'", async () => {
    expect(await sha256HexOfBuffer(new TextEncoder().encode("abc"))).toBe(ABC);
  });

  it("liczy skrót pustego ciągu", async () => {
    expect(await sha256HexOfBuffer(new Uint8Array())).toBe(EMPTY);
  });

  it("liczy skrót pliku (Blob)", async () => {
    expect(await sha256HexOfBlob(new Blob(["abc"]))).toBe(ABC);
  });

  it("dzieli skrót na 8 grup po 8 znaków", () => {
    const groups = groupHash(ABC);
    expect(groups).toHaveLength(8);
    expect(groups.every((group) => group.length === 8)).toBe(true);
    expect(groups.join("")).toBe(ABC);
  });

  it("normalizuje wielkość liter, spacje i prefiks", () => {
    expect(
      normalizeHash(`  SHA256:${ABC.toUpperCase().slice(0, 8)} ${ABC.toUpperCase().slice(8)} `),
    ).toBe(ABC);
  });

  it("porównuje skróty niezależnie od zapisu", () => {
    expect(hashesMatch(ABC.toUpperCase(), ABC)).toBe(true);
    expect(hashesMatch(ABC, EMPTY)).toBe(false);
  });

  it("rozpoznaje poprawny zapis SHA-256", () => {
    expect(isSha256Hex(ABC)).toBe(true);
    expect(isSha256Hex("abc")).toBe(false);
  });

  it("skraca skrót do tabel", () => {
    expect(shortHash(ABC)).toBe("ba7816bf…");
  });
});

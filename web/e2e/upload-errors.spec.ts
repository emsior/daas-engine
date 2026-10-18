import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { SAMPLE_CSV, signUp } from "./helpers";

/** Wzorce komunikatów uploadu (GOV.UK): typ, rozmiar, pusty plik, brak kolumn, separator/kodowanie. */
const sampleHeader = readFileSync(SAMPLE_CSV, "utf8").split("\n")[0] ?? "";

async function choose(page: Page, file: { name: string; mimeType: string; buffer: Buffer }) {
  await page.setInputFiles("#upload-file", file);
}

async function expectFileError(page: Page, text: string | RegExp) {
  // komunikat przy polu i w podsumowaniu błędów (z fokusem), z tym samym tekstem
  await expect(page.locator("#upload-file-error")).toContainText(text);
  await expect(page.getByRole("group", { name: "Wystąpił problem" })).toBeFocused();
}

test.describe("komunikaty błędów uploadu", () => {
  test.beforeEach(async ({ page }) => {
    await signUp(page, "upload-errors");
    await page.goto("/upload");
  });

  test("typ pliku", async ({ page }) => {
    await choose(page, { name: "zdjecie.png", mimeType: "image/png", buffer: Buffer.from("x") });
    await expectFileError(page, /Plik musi być w formacie CSV, XLSX lub JSON/);
  });

  test("rozmiar", async ({ page }) => {
    await choose(page, {
      name: "duzy.csv",
      mimeType: "text/csv",
      buffer: Buffer.alloc(10 * 1024 * 1024 + 1, "a"),
    });
    await expectFileError(page, /Plik musi mieć mniej niż 10[\s ]MB/);
  });

  test("pusty plik", async ({ page }) => {
    await choose(page, { name: "pusty.csv", mimeType: "text/csv", buffer: Buffer.alloc(0) });
    await expectFileError(page, "Wybrany plik jest pusty");
  });

  test("brak wymaganych kolumn (odpowiedź serwera)", async ({ page }) => {
    await choose(page, {
      name: "inne.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("imie;nazwisko\nAnna;Nowak\n"),
    });
    await page.getByRole("button", { name: "Sprawdź plik" }).click();
    await expectFileError(page, /brakuje wymaganych kolumn/);
    // plik został zachowany w formularzu, można poprawić i ponowić
    await expect(page.getByRole("button", { name: "Sprawdź plik" })).toBeEnabled();
  });

  test("brak wierszy / zły separator", async ({ page }) => {
    await choose(page, {
      name: "naglowek.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(`${sampleHeader}\n`),
    });
    await page.getByRole("button", { name: "Sprawdź plik" }).click();
    await expectFileError(page, /Sprawdź nagłówki i separator/);
  });

  test("nieczytelne kodowanie", async ({ page }) => {
    await choose(page, {
      name: "binarny.csv",
      mimeType: "text/csv",
      buffer: Buffer.from([0xff, 0xfe, 0x00, 0x80, 0x81, 0x82, 0xfd, 0x0a, 0xc3, 0x28, 0x0a]),
    });
    await page.getByRole("button", { name: "Sprawdź plik" }).click();
    await expectFileError(page, /Nie udało się odczytać pliku.*UTF-8.*średnik lub przecinek/);
  });
});

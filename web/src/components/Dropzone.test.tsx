import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/utils";
import { Dropzone } from "./Dropzone";

function setup(props: Partial<React.ComponentProps<typeof Dropzone>> = {}) {
  const onFile = vi.fn();
  const onReject = vi.fn();
  renderWithProviders(
    <Dropzone
      id="file"
      title="Przeciągnij plik tutaj"
      orText="albo"
      buttonLabel="Wybierz plik"
      hint="Dozwolone: CSV, XLSX, JSON."
      onFile={onFile}
      onReject={onReject}
      {...props}
    />,
  );
  const input = document.getElementById("file") as HTMLInputElement;
  return { onFile, onReject, input };
}

const csv = (name = "dane.csv", size = 10) =>
  new File([new Uint8Array(size).fill(65)], name, { type: "text/csv" });

describe("Dropzone", () => {
  it("przyjmuje poprawny plik CSV", async () => {
    const { onFile, onReject, input } = setup();
    await userEvent.upload(input, csv());
    expect(onFile).toHaveBeenCalledOnce();
    expect(onReject).not.toHaveBeenCalled();
  });

  it("odrzuca niewłaściwy typ pliku", () => {
    const { onFile, onReject, input } = setup();
    fireEvent.change(input, { target: { files: [new File(["x"], "zdjecie.png")] } });
    expect(onFile).not.toHaveBeenCalled();
    expect(onReject.mock.calls[0]?.[0]).toMatchObject({ code: "type", extension: "png" });
  });

  it("odrzuca .xlsm jako niedozwolony typ", () => {
    const { onReject, input } = setup();
    fireEvent.change(input, { target: { files: [new File(["x"], "makra.xlsm")] } });
    expect(onReject.mock.calls[0]?.[0]).toMatchObject({ code: "type" });
  });

  it("odrzuca plik pusty", () => {
    const { onReject, input } = setup();
    fireEvent.change(input, { target: { files: [new File([], "pusty.csv")] } });
    expect(onReject.mock.calls[0]?.[0]).toMatchObject({ code: "empty" });
  });

  it("odrzuca plik większy niż 10 MiB", () => {
    const { onReject, input } = setup();
    const big = { name: "duzy.csv", size: 10 * 1024 * 1024 + 1 } as File;
    fireEvent.change(input, { target: { files: [big] } });
    expect(onReject.mock.calls[0]?.[0]).toMatchObject({
      code: "size",
      limitBytes: 10 * 1024 * 1024,
    });
  });

  it("paczka JSON ma limit 5 MiB", () => {
    const { onReject, input } = setup();
    fireEvent.change(input, {
      target: { files: [{ name: "paczka.json", size: 6 * 1024 * 1024 } as File] },
    });
    expect(onReject.mock.calls[0]?.[0]).toMatchObject({
      code: "size",
      limitBytes: 5 * 1024 * 1024,
    });
  });

  it("jest dostępna z klawiatury: pole pliku dostaje fokus przez Tab i ma nazwę przycisku", async () => {
    const { input } = setup();
    await userEvent.tab();
    expect(input).toHaveFocus();
    expect(screen.getByLabelText("Wybierz plik")).toBe(input);
    expect(input).not.toHaveAttribute("tabindex", "-1");
  });

  it("opisuje pole podpowiedzią i błędem (aria-describedby) oraz ustawia stan error", () => {
    const { input } = setup({ error: "Plik jest pusty." });
    expect(input.getAttribute("aria-describedby")).toContain("file-error");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById("file-error")).toHaveTextContent("Plik jest pusty.");
    expect(document.querySelector(".dropzone")).toHaveAttribute("data-state", "error");
  });

  it("przeciąganie zmienia stan na active, upuszczenie przekazuje plik", () => {
    const { onFile } = setup();
    const zone = document.querySelector(".dropzone") as HTMLElement;
    fireEvent.dragEnter(zone);
    expect(zone).toHaveAttribute("data-state", "active");
    fireEvent.drop(zone, { dataTransfer: { files: [csv("z-drop.csv")] } });
    expect(zone).toHaveAttribute("data-state", "idle");
    expect(onFile).toHaveBeenCalledOnce();
  });

  it("nie przyjmuje plików, gdy jest wyłączona", () => {
    const { onFile, input } = setup({ disabled: true });
    expect(input).toBeDisabled();
    const zone = document.querySelector(".dropzone") as HTMLElement;
    fireEvent.drop(zone, { dataTransfer: { files: [csv()] } });
    expect(onFile).not.toHaveBeenCalled();
  });
});

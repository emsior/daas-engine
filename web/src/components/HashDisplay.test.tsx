import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/utils";
import { CopyButton } from "./CopyButton";
import { HashDisplay } from "./HashDisplay";

const HASH = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

describe("HashDisplay", () => {
  it("renderuje 8 grup po 8 znaków z <wbr> między grupami", () => {
    const { container } = renderWithProviders(<HashDisplay hash={HASH} />);
    const groups = container.querySelectorAll(".hash-group");
    expect(groups).toHaveLength(8);
    expect(container.querySelectorAll("wbr")).toHaveLength(7);
    expect(container.querySelector("code")).toHaveClass("hash");
  });

  it("zachowuje ciągły tekst skrótu (kopiowanie bez spacji)", () => {
    const { container } = renderWithProviders(<HashDisplay hash={HASH} />);
    expect(container.querySelector("code")?.textContent).toBe(HASH);
  });
});

describe("CopyButton", () => {
  it("kopiuje wartość i ogłasza potwierdzenie w regionie aria-live", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderWithProviders(<CopyButton value={HASH} label="Kopiuj całość" />);
    await userEvent.click(screen.getByRole("button", { name: "Kopiuj całość" }));
    expect(writeText).toHaveBeenCalledWith(HASH);
    expect(screen.getByTestId("live-region")).toHaveTextContent("Skopiowano do schowka.");
    expect(screen.getByRole("button", { name: "Skopiowano" })).toBeInTheDocument();
  });
});

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "@/test/utils";
import { Dialog } from "./Dialog";

function Host() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Otwórz</button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Odwołać klucz?"
        footer={<button>Potwierdź</button>}
      >
        <p>Treść</p>
        <input aria-label="pole" />
      </Dialog>
    </>
  );
}

describe("Dialog", () => {
  it("ma role dialog, aria-modal i tytuł; fokus trafia do środka", async () => {
    renderWithProviders(<Host />);
    await userEvent.click(screen.getByRole("button", { name: "Otwórz" }));
    const dialog = screen.getByRole("dialog", { name: "Odwołać klucz?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("zatrzymuje fokus wewnątrz (Tab i Shift+Tab zawijają)", async () => {
    renderWithProviders(<Host />);
    await userEvent.click(screen.getByRole("button", { name: "Otwórz" }));
    const input = screen.getByLabelText("pole");
    const confirm = screen.getByRole("button", { name: "Potwierdź" });
    input.focus();
    await userEvent.tab();
    expect(confirm).toHaveFocus();
    await userEvent.tab();
    expect(input).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(confirm).toHaveFocus();
  });

  it("zamyka się klawiszem Escape i oddaje fokus elementowi otwierającemu", async () => {
    renderWithProviders(<Host />);
    const opener = screen.getByRole("button", { name: "Otwórz" });
    await userEvent.click(opener);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  });
});

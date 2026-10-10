import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "@/test/utils";
import { ErrorSummary } from "./ErrorSummary";
import { Field } from "./Field";
import { Input } from "./Input";

describe("Field", () => {
  it("wiąże etykietę z polem i opisuje je podpowiedzią", () => {
    renderWithProviders(
      <Field id="email" label="Adres e-mail" hint="Np. imie@firma.pl">
        {(control) => <Input {...control} />}
      </Field>,
    );
    const input = screen.getByLabelText("Adres e-mail");
    expect(input).toHaveAttribute("aria-describedby", "email-hint");
    expect(input).not.toHaveAttribute("aria-invalid");
  });

  it("przy błędzie ustawia aria-invalid i opisuje pole podpowiedzią oraz błędem", () => {
    renderWithProviders(
      <Field id="email" label="Adres e-mail" hint="Podpowiedź" error="Podaj adres e-mail.">
        {(control) => <Input {...control} />}
      </Field>,
    );
    const input = screen.getByLabelText("Adres e-mail");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", "email-hint email-error");
    expect(document.getElementById("email-error")).toHaveTextContent("Podaj adres e-mail.");
  });

  it("oznacza pole wymagane atrybutem aria-required", () => {
    renderWithProviders(
      <Field id="n" label="Nazwa" required>
        {(control) => <Input {...control} />}
      </Field>,
    );
    expect(screen.getByLabelText("Nazwa")).toHaveAttribute("aria-required", "true");
  });
});

describe("ErrorSummary", () => {
  const errors = [
    { id: "email", message: "Podaj adres e-mail." },
    { id: "dpa", message: "Zaakceptuj umowę." },
  ];

  it("przenosi fokus na podsumowanie przy montowaniu", () => {
    renderWithProviders(<ErrorSummary title="Wystąpił problem" errors={errors} />);
    expect(screen.getByRole("group", { name: "Wystąpił problem" })).toHaveFocus();
  });

  it("przenosi fokus ponownie po zmianie focusKey", () => {
    const { rerender } = renderWithProviders(
      <ErrorSummary title="Wystąpił problem" errors={errors} focusKey={1} />,
    );
    const summary = screen.getByRole("group", { name: "Wystąpił problem" });
    (document.activeElement as HTMLElement).blur();
    expect(summary).not.toHaveFocus();
    rerender(<ErrorSummary title="Wystąpił problem" errors={errors} focusKey={2} />);
    expect(summary).toHaveFocus();
  });

  it("link błędu przenosi fokus na pole", async () => {
    renderWithProviders(
      <>
        <ErrorSummary title="Wystąpił problem" errors={errors} />
        <input id="email" aria-label="e-mail" />
      </>,
    );
    await userEvent.click(screen.getByRole("link", { name: "Podaj adres e-mail." }));
    expect(screen.getByLabelText("e-mail")).toHaveFocus();
  });

  it("nie renderuje się bez błędów i nie ma role=alert", () => {
    const { container } = renderWithProviders(<ErrorSummary title="x" errors={[]} />);
    expect(container.querySelector(".error-summary")).toBeNull();
    renderWithProviders(<ErrorSummary title="Tytuł" errors={errors} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

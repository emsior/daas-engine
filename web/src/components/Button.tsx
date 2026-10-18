import Link from "next/link";
import type { ComponentProps } from "react";
import { cx } from "@/lib/cx";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary: "",
  secondary: "btn-secondary",
  ghost: "btn-ghost",
  danger: "btn-danger",
};

export interface ButtonProps extends ComponentProps<"button"> {
  variant?: ButtonVariant;
  /** Główne wezwanie do działania: 44 px na mobile, pełna szerokość < 640 px. */
  cta?: boolean;
  /**
   * Stan ładowania: `aria-busy` + `aria-disabled` (przycisk zachowuje fokus), kliknięcia są ignorowane.
   * Zmień też tekst przycisku (np. „Wysyłam”), a CSS doda wielokropek.
   */
  loading?: boolean;
}

export function Button({
  variant = "primary",
  cta,
  loading,
  className,
  onClick,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      className={cx("btn", VARIANT_CLASS[variant], cta && "btn-cta", className)}
      aria-busy={loading ? true : undefined}
      aria-disabled={loading || rest["aria-disabled"] ? true : undefined}
      onClick={(event) => {
        if (loading) {
          event.preventDefault();
          return;
        }
        onClick?.(event);
      }}
    />
  );
}

export interface LinkButtonProps extends Omit<ComponentProps<typeof Link>, "className"> {
  variant?: ButtonVariant;
  cta?: boolean;
  className?: string;
}

/** Link wyglądający jak przycisk (nawigacja, np. „Wgraj plik”). */
export function LinkButton({ variant = "primary", cta, className, ...rest }: LinkButtonProps) {
  return (
    <Link {...rest} className={cx("btn", VARIANT_CLASS[variant], cta && "btn-cta", className)} />
  );
}

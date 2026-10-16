import type { ComponentProps } from "react";
import { cx } from "@/lib/cx";

export interface InputProps extends ComponentProps<"input"> {
  /** Ustawia `aria-invalid` i obrys błędu. */
  invalid?: boolean;
}

export function Input({ invalid, className, ...rest }: InputProps) {
  return (
    <input
      {...rest}
      className={cx("input", className)}
      aria-invalid={invalid ? true : rest["aria-invalid"]}
    />
  );
}

export interface SelectProps extends ComponentProps<"select"> {
  invalid?: boolean;
}

export function Select({ invalid, className, ...rest }: SelectProps) {
  return (
    <select
      {...rest}
      className={cx("input", "select", className)}
      aria-invalid={invalid ? true : rest["aria-invalid"]}
    />
  );
}

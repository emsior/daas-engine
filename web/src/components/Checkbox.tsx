import type { ComponentProps, ReactNode } from "react";
import { cx } from "@/lib/cx";

export interface CheckboxProps extends Omit<ComponentProps<"input">, "type"> {
  label: ReactNode;
  hint?: ReactNode;
  invalid?: boolean;
}

/** Pole wyboru z etykietą klikalną w całości (cel >= 24 px). Opis przez `aria-describedby`. */
export function Checkbox({ label, hint, invalid, id, className, ...rest }: CheckboxProps) {
  const hintId = hint && id ? `${id}-hint` : undefined;
  const describedBy = [rest["aria-describedby"], hintId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cx("check", className)}>
      <input
        {...rest}
        id={id}
        type="checkbox"
        className="check__input"
        aria-invalid={invalid ? true : rest["aria-invalid"]}
        aria-describedby={describedBy}
      />
      <div className="check__body">
        <label htmlFor={id} className="check__label">
          {label}
        </label>
        {hint ? (
          <div className="hint" id={hintId}>
            {hint}
          </div>
        ) : null}
      </div>
    </div>
  );
}

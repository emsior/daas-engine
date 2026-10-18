import type { ComponentProps, ReactNode } from "react";
import { cx } from "@/lib/cx";

export type AlertVariant = "info" | "success" | "warning" | "danger";

export interface AlertProps extends Omit<ComponentProps<"div">, "title"> {
  variant?: AlertVariant;
  title?: ReactNode;
}

/**
 * Alert: ikona kołowa (CSS) + gruby lewy brzeg + tekst.
 * Wewnątrz jest dokładnie jeden element potomny (wymóg siatki w design systemie).
 * Dla komunikatów statusu dodaj `role="status"`; błędy blokujące idą do ErrorSummary.
 */
export function Alert({ variant = "info", title, className, children, ...rest }: AlertProps) {
  return (
    <div {...rest} className={cx("alert", `alert-${variant}`, className)}>
      <div>
        {title ? <strong className="alert-title">{title}</strong> : null}
        {children}
      </div>
    </div>
  );
}

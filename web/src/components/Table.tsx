import type { ComponentProps, ReactNode } from "react";
import { cx } from "@/lib/cx";

export interface TableProps {
  /** Opis tabeli dla czytników (caption); obejmuje też region przewijany. */
  caption: string;
  children: ReactNode;
  className?: string;
}

/**
 * Tabela responsywna: na < 640 px wiersze są kartami (etykiety z `data-label`).
 * `display: block` odbiera semantykę części czytników, dlatego role są jawne.
 */
export function Table({ caption, children, className }: TableProps) {
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={caption}>
      <table className={cx("data-table", className)} role="table">
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export function Thead(props: ComponentProps<"thead">) {
  return <thead {...props} role="rowgroup" />;
}

export function Tbody(props: ComponentProps<"tbody">) {
  return <tbody {...props} role="rowgroup" />;
}

export function Tr(props: ComponentProps<"tr">) {
  return <tr {...props} role="row" />;
}

export function Th({ rowHeader, ...rest }: ComponentProps<"th"> & { rowHeader?: boolean }) {
  return (
    <th
      scope={rowHeader ? "row" : "col"}
      {...rest}
      role={rowHeader ? "rowheader" : "columnheader"}
    />
  );
}

export interface TdProps extends ComponentProps<"td"> {
  /** Etykieta kolumny pokazywana w układzie kartowym (ten sam tekst co nagłówek). */
  label: string;
  /** Kolumna liczbowa: wyrównanie do prawej, cyfry tabelaryczne. */
  numeric?: boolean;
}

export function Td({ label, numeric, className, ...rest }: TdProps) {
  return (
    <td {...rest} role="cell" data-label={label} className={cx(numeric && "num", className)} />
  );
}

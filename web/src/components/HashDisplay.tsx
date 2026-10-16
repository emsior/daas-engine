import { Fragment } from "react";
import { groupHash } from "@/lib/hash";
import { cx } from "@/lib/cx";

export interface HashDisplayProps {
  /** Skrót w zapisie szesnastkowym (64 znaki dla SHA-256). */
  hash: string;
  className?: string;
}

/**
 * Skrót w grupach po 8 znaków: `<span class="hash-group">` rozdzielone `<wbr>` bez spacji,
 * więc zaznaczenie i kopiowanie daje ciągły ciąg, a na 375 px zawija się na granicy grup.
 */
export function HashDisplay({ hash, className }: HashDisplayProps) {
  const groups = groupHash(hash, 8);
  return (
    <code className={cx("hash", className)} data-hash={hash}>
      {groups.map((group, index) => (
        <Fragment key={`${index}-${group}`}>
          <span className="hash-group">{group}</span>
          {index < groups.length - 1 ? <wbr /> : null}
        </Fragment>
      ))}
    </code>
  );
}

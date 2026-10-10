"use client";

import type { ReactNode } from "react";
import { useT } from "@/i18n/provider";
import { describeError } from "@/lib/errors";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { Skeleton } from "./Skeleton";

export interface QueryLike<T> {
  data: T | undefined;
  error: Error | null;
  isPending: boolean;
  isFetching: boolean;
  refetch: () => unknown;
}

export interface QueryStateProps<T> {
  query: QueryLike<T>;
  /** Tekst dla czytników w trakcie ładowania. */
  loadingLabel: string;
  errorTitle: string;
  /** Własny szkielet (domyślnie 3 linie). */
  skeleton?: ReactNode;
  /** Czy dane są puste; wtedy pokazujemy `empty`. */
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  children: (data: T) => ReactNode;
}

/**
 * Cztery stany widoku danych: loading (szkielet, `aria-busy`), error (komunikat + ponów),
 * empty (opis + CTA) i success (dane). Nigdy nie renderuje `undefined`.
 */
export function QueryState<T>({
  query,
  loadingLabel,
  errorTitle,
  skeleton,
  isEmpty,
  empty,
  children,
}: QueryStateProps<T>) {
  const t = useT();

  if (query.isPending) {
    return (
      <div aria-busy="true" className="stack-3">
        <p role="status" className="sr-only">
          {loadingLabel}
        </p>
        {skeleton ?? <Skeleton lines={3} />}
      </div>
    );
  }

  if (query.error || query.data === undefined) {
    const { message, requestId } = describeError(query.error, t);
    return (
      <Alert variant="danger" role="alert" title={errorTitle}>
        <div className="stack-3">
          <p>{message}</p>
          {requestId ? <p className="text-muted">{t("app.requestId", { id: requestId })}</p> : null}
          <div>
            <Button variant="secondary" loading={query.isFetching} onClick={() => query.refetch()}>
              {t("app.retry")}
            </Button>
          </div>
        </div>
      </Alert>
    );
  }

  if (isEmpty?.(query.data)) return <>{empty}</>;
  return <>{children(query.data)}</>;
}

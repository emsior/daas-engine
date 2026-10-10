"use client";

import { Alert } from "@/components/Alert";
import { Button, LinkButton } from "@/components/Button";
import { useT } from "@/i18n/provider";

/** Granica błędów renderowania: komunikat + ponów + powrót do kokpitu. */
export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useT();
  return (
    <main id="main" tabIndex={-1} className="layout-container container-narrow app-main">
      <div className="view">
        <h1 tabIndex={-1}>{t("error.page.title")}</h1>
        <Alert variant="danger" role="alert">
          <div className="stack-3">
            <p>{t("error.page.body")}</p>
            <div className="cluster">
              <Button onClick={reset}>{t("app.retry")}</Button>
              <LinkButton href="/" variant="secondary">
                {t("notFound.cta")}
              </LinkButton>
            </div>
          </div>
        </Alert>
      </div>
    </main>
  );
}

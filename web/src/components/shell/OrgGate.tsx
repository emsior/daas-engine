"use client";

import { createContext, useContext, useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Alert } from "@/components/Alert";
import { Button } from "@/components/Button";
import { Skeleton } from "@/components/Skeleton";
import { useT } from "@/i18n/provider";
import { isApiError } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks";
import type { Me, Org } from "@/lib/api/types";
import { describeError } from "@/lib/errors";

interface Session {
  me: Me;
  org: Org;
  orgId: string;
}

const SessionContext = createContext<Session | null>(null);

/** Zalogowany użytkownik i jego organizacja; dostępne tylko wewnątrz `<OrgGate>`. */
export function useSession(): Session {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession() wymaga <OrgGate>");
  return value;
}

/**
 * Bramka sesji w widokach aplikacji. Pierwszą linią obrony jest proxy (przekierowanie na /login),
 * tutaj obsługujemy wygaśnięcie sesji w trakcie pracy oraz użytkownika bez organizacji.
 */
export function OrgGate({ children }: { children: ReactNode }) {
  const t = useT();
  const router = useRouter();
  const query = useMe();
  const unauthorized = isApiError(query.error) && query.error.status === 401;
  const needsOnboarding = query.data !== undefined && query.data.org === null;

  useEffect(() => {
    if (unauthorized) router.replace("/login");
    else if (needsOnboarding) router.replace("/onboarding");
  }, [unauthorized, needsOnboarding, router]);

  if (query.isPending || unauthorized || needsOnboarding) {
    return (
      <div aria-busy="true" className="stack-3">
        <p role="status" className="sr-only">
          {t("app.loading")}
        </p>
        <Skeleton lines={4} />
      </div>
    );
  }

  if (query.error || !query.data || !query.data.org) {
    const { message, requestId } = describeError(query.error, t);
    return (
      <Alert variant="danger" title={t("error.title")}>
        <div className="stack-3">
          <p>{message}</p>
          {requestId ? <p className="text-muted">{t("app.requestId", { id: requestId })}</p> : null}
          <div>
            <Button variant="secondary" onClick={() => query.refetch()}>
              {t("app.retry")}
            </Button>
          </div>
        </div>
      </Alert>
    );
  }

  const session: Session = { me: query.data, org: query.data.org, orgId: query.data.org.id };
  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}

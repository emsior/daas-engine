"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import type { Locale } from "@/lib/api/types";
import { I18nProvider } from "@/i18n/provider";
import type { Messages } from "@/i18n";
import { isApiError } from "@/lib/api/client";
import { AnnouncerProvider } from "./Announcer";

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (isApiError(error) && error.status >= 400 && error.status < 500) return false;
          return failureCount < 2;
        },
      },
    },
  });
}

/**
 * Po zmianie trasy przenosi fokus na `h1` nowego widoku (WCAG 2.4.3 / wytyczne systemu).
 * Pierwsze wejście na stronę (pełne ładowanie) nie przenosi fokusu.
 */
function RouteFocus() {
  const pathname = usePathname();
  const previous = useRef<string | null>(null);

  useEffect(() => {
    if (previous.current !== null && previous.current !== pathname) {
      document.querySelector<HTMLElement>("main h1")?.focus();
    }
    previous.current = pathname;
  }, [pathname]);

  return null;
}

export function Providers({
  locale,
  messages,
  children,
}: {
  locale: Locale;
  messages: Messages;
  children: ReactNode;
}) {
  const [client] = useState(makeQueryClient);
  return (
    <I18nProvider locale={locale} messages={messages}>
      <QueryClientProvider client={client}>
        <AnnouncerProvider>
          <RouteFocus />
          {children}
        </AnnouncerProvider>
      </QueryClientProvider>
    </I18nProvider>
  );
}

import { useEffect } from "react";
import { APP_NAME } from "@/lib/env";

/** Ustawia tytuł karty: `Tytuł widoku · DaaS Engine Cloud` (WCAG 2.4.2). */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    if (title) document.title = `${title} · ${APP_NAME}`;
  }, [title]);
}

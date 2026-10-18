"use client";

import type { ReactNode } from "react";
import type { ThemeChoice } from "@/components/ThemeToggle";
import { useMe } from "@/lib/api/hooks";
import { AppShell } from "./AppShell";
import { OrgGate } from "./OrgGate";

/** Rama aplikacji: nagłówek z nazwą organizacji + bramka sesji wokół treści widoku. */
export function AppFrame({ theme, children }: { theme: ThemeChoice; children: ReactNode }) {
  const me = useMe();
  return (
    <AppShell orgName={me.data?.org?.name ?? null} theme={theme}>
      <OrgGate>{children}</OrgGate>
    </AppShell>
  );
}

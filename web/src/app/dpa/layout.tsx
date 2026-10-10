import type { ReactNode } from "react";
import { AuthShell } from "@/components/shell/AuthShell";
import { dictionaries, translate } from "@/i18n";
import { getPreferences } from "@/lib/server/preferences";

export default async function DpaLayout({ children }: { children: ReactNode }) {
  const { locale, theme } = await getPreferences();
  return (
    <AuthShell theme={theme} brand={translate(dictionaries[locale], "app.name")}>
      {children}
    </AuthShell>
  );
}

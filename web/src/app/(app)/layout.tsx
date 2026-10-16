import type { ReactNode } from "react";
import { AppFrame } from "@/components/shell/AppFrame";
import { getPreferences } from "@/lib/server/preferences";

export default async function AppLayout({ children }: { children: ReactNode }) {
  const { theme } = await getPreferences();
  return <AppFrame theme={theme}>{children}</AppFrame>;
}

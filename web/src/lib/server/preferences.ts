import { cookies, headers } from "next/headers";
import type { ThemeChoice } from "@/components/ThemeToggle";
import { normalizeLocale } from "@/i18n";
import { LANG_COOKIE, THEME_COOKIE } from "@/lib/env";
import type { Locale } from "@/lib/api/types";

export interface Preferences {
  locale: Locale;
  theme: ThemeChoice;
}

/** Język i motyw z żądania: nagłówek ustawiony przez proxy (?lang=) ma pierwszeństwo przed ciasteczkiem. */
export async function getPreferences(): Promise<Preferences> {
  const [jar, incoming] = await Promise.all([cookies(), headers()]);
  const locale = normalizeLocale(incoming.get("x-daas-lang") ?? jar.get(LANG_COOKIE)?.value);
  const stored = jar.get(THEME_COOKIE)?.value;
  const theme: ThemeChoice = stored === "light" || stored === "dark" ? stored : "system";
  return { locale, theme };
}

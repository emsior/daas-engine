import pl from "./pl.json";
import en from "./en.json";
import type { Locale } from "@/lib/api/types";

export type Messages = typeof pl;
export type MessageKey = keyof Messages;
export type Vars = Record<string, string | number>;

export const LOCALES: readonly Locale[] = ["pl", "en"];
export const DEFAULT_LOCALE: Locale = "pl";

export const dictionaries: Record<Locale, Messages> = { pl, en };

export function normalizeLocale(value: string | null | undefined): Locale {
  return value === "en" ? "en" : DEFAULT_LOCALE;
}

/** Podstawia `{nazwa}` wartościami z `vars`; nieznane znaczniki zostają bez zmian. */
export function translate(messages: Messages, key: MessageKey, vars?: Vars): string {
  const template: string = messages[key] ?? key;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = vars[name];
    return value === undefined ? match : String(value);
  });
}

/** Tłumaczenie klucza zbudowanego dynamicznie (np. `pipeline.${name}`); brak klucza = `fallback`. */
export function translateDynamic(
  messages: Messages,
  key: string,
  fallback: string,
  vars?: Vars,
): string {
  if (key in messages) return translate(messages, key as MessageKey, vars);
  return fallback;
}

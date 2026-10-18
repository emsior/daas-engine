"use client";

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import type { Locale } from "@/lib/api/types";
import { translate, translateDynamic, type MessageKey, type Messages, type Vars } from "./index";

interface I18nValue {
  locale: Locale;
  messages: Messages;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({
  locale,
  messages,
  children,
}: {
  locale: Locale;
  messages: Messages;
  children: ReactNode;
}) {
  const value = useMemo(() => ({ locale, messages }), [locale, messages]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useT() wymaga <I18nProvider>");
  return value;
}

export type TFunction = (key: MessageKey, vars?: Vars) => string;
export type TDynamicFunction = (key: string, fallback: string, vars?: Vars) => string;

/** Hook tłumaczeń: `const t = useT(); t("login.title")`. */
export function useT(): TFunction {
  const { messages } = useI18n();
  return useCallback((key, vars) => translate(messages, key, vars), [messages]);
}

/** Tłumaczenie klucza złożonego w locie (statusy, nazwy pipeline'ów) z wartością zastępczą. */
export function useTDynamic(): TDynamicFunction {
  const { messages } = useI18n();
  return useCallback(
    (key, fallback, vars) => translateDynamic(messages, key, fallback, vars),
    [messages],
  );
}

export function useLocale(): Locale {
  return useI18n().locale;
}

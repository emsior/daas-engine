"use client";

import { usePathname } from "next/navigation";
import { useLocale, useT } from "@/i18n/provider";
import { LOCALES } from "@/i18n";

/**
 * Przełącznik języka PL / EN. To zwykłe linki z `?lang=` (pełne przeładowanie):
 * proxy zapisuje wybór w ciasteczku `lang`, a layout renderuje słownik po stronie serwera.
 */
export function LangToggle() {
  const t = useT();
  const locale = useLocale();
  const pathname = usePathname();

  return (
    <div role="group" aria-label={t("lang.label")} className="cluster-2">
      {LOCALES.map((option) => (
        <a
          key={option}
          href={`${pathname}?lang=${option}`}
          lang={option}
          hrefLang={option}
          className="lang-link"
          aria-current={option === locale ? "true" : undefined}
        >
          {option.toUpperCase()}
          <span className="sr-only"> {t(`lang.${option}`)}</span>
        </a>
      ))}
    </div>
  );
}

"use client";

import { useState } from "react";
import { useT } from "@/i18n/provider";
import { THEME_COOKIE } from "@/lib/env";
import { writeCookie } from "@/lib/cookies";
import { Button } from "./Button";
import { useAnnounce } from "./Announcer";

export type ThemeChoice = "system" | "light" | "dark";

const CHOICES: ThemeChoice[] = ["system", "light", "dark"];

/** Stosuje wybór na `<html>`: brak atrybutu = preferencja systemu (tokeny reagują same). */
export function applyTheme(
  choice: ThemeChoice,
  root: HTMLElement = document.documentElement,
): void {
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

/**
 * Trzy przyciski z etykietami tekstowymi i `aria-pressed`. Wybór trafia do ciasteczka `theme`,
 * które layout czyta po stronie serwera (bez skryptu inline i bez błysku złego motywu).
 */
export function ThemeToggle({ initial = "system" }: { initial?: ThemeChoice }) {
  const t = useT();
  const announce = useAnnounce();
  const [choice, setChoice] = useState<ThemeChoice>(initial);

  const select = (next: ThemeChoice) => {
    setChoice(next);
    applyTheme(next);
    writeCookie(THEME_COOKIE, next);
    announce(`${t("theme.label")}: ${t(`theme.${next}`)}`);
  };

  return (
    <div role="group" aria-label={t("theme.label")} className="cluster-2">
      <span className="text-muted" aria-hidden="true">
        {t("theme.label")}:
      </span>
      {CHOICES.map((option) => (
        <Button
          key={option}
          variant="ghost"
          aria-pressed={choice === option}
          onClick={() => select(option)}
        >
          {t(`theme.${option}`)}
        </Button>
      ))}
    </div>
  );
}

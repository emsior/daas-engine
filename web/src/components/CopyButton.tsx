"use client";

import { useEffect, useRef, useState } from "react";
import { useT } from "@/i18n/provider";
import { copyText } from "@/lib/clipboard";
import { Button, type ButtonVariant } from "./Button";
import { useAnnounce } from "./Announcer";

export interface CopyButtonProps {
  value: string;
  /** Widoczny tekst przycisku; domyślnie „Kopiuj”. */
  label?: string;
  /** Pełna nazwa dla czytników, gdy na stronie jest wiele przycisków „Kopiuj”. */
  accessibleName?: string;
  variant?: ButtonVariant;
  className?: string;
}

/** Przycisk kopiujący; potwierdzenie widoczne w przycisku i ogłoszone w regionie aria-live. */
export function CopyButton({
  value,
  label,
  accessibleName,
  variant = "secondary",
  className,
}: CopyButtonProps) {
  const t = useT();
  const announce = useAnnounce();
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const handleClick = async () => {
    const ok = await copyText(value);
    setState(ok ? "copied" : "failed");
    announce(ok ? t("copy.announce") : t("copy.failed"));
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 5000);
  };

  const text = state === "copied" ? t("copy.done") : (label ?? t("copy.button"));

  return (
    <span className="cluster-2">
      <Button
        variant={variant}
        className={className}
        onClick={handleClick}
        aria-label={accessibleName && state === "idle" ? accessibleName : undefined}
      >
        {text}
      </Button>
      {state === "failed" ? (
        <span className="field-error">
          <span>{t("copy.failed")}</span>
        </span>
      ) : null}
    </span>
  );
}

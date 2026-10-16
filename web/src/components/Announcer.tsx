"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

type Announce = (message: string) => void;

const AnnouncerContext = createContext<Announce | null>(null);

/**
 * Jeden stały region `aria-live="polite"` w layoucie, poza fragmentami podmienianymi.
 * Komunikaty („Skopiowano”, „Plik wgrany”) wpisuje się przez `useAnnounce()`.
 */
export function AnnouncerProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState("");
  const [toggle, setToggle] = useState(false);

  const announce = useCallback<Announce>((text) => {
    setMessage(text);
    setToggle((value) => !value); // pozwala ogłosić ten sam tekst dwa razy z rzędu
  }, []);

  const value = useMemo(() => announce, [announce]);

  return (
    <AnnouncerContext.Provider value={value}>
      {children}
      <div role="status" aria-live="polite" className="sr-only" data-testid="live-region">
        {message}
        {toggle ? "​" : ""}
      </div>
    </AnnouncerContext.Provider>
  );
}

const noop: Announce = () => undefined;

export function useAnnounce(): Announce {
  return useContext(AnnouncerContext) ?? noop;
}

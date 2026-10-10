import { useEffect, useRef, useState } from "react";

/**
 * Zwraca wartość odświeżaną najwyżej raz na `intervalMs`.
 * Służy do ogłoszeń `aria-live` (postęp nie może być czytany przy każdym wierszu).
 */
export function useThrottledValue<T>(value: T, intervalMs: number): T {
  const [shown, setShown] = useState(value);
  const lastUpdate = useRef(0);

  useEffect(() => {
    const wait = Math.max(0, intervalMs - (Date.now() - lastUpdate.current));
    const timer = setTimeout(() => {
      lastUpdate.current = Date.now();
      setShown(value);
    }, wait);
    return () => clearTimeout(timer);
  }, [value, intervalMs]);

  return shown;
}

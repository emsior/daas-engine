/** Proste operacje na ciasteczkach po stronie przeglądarki (bez bibliotek). */

export function readCookie(name: string, source?: string): string | null {
  const jar = source ?? (typeof document === "undefined" ? "" : document.cookie);
  if (!jar) return null;
  for (const part of jar.split(";")) {
    const index = part.indexOf("=");
    if (index === -1) continue;
    const key = part.slice(0, index).trim();
    if (key === name) {
      try {
        return decodeURIComponent(part.slice(index + 1).trim());
      } catch {
        return part.slice(index + 1).trim();
      }
    }
  }
  return null;
}

export function writeCookie(name: string, value: string, days = 365): void {
  if (typeof document === "undefined") return;
  const secure =
    typeof location !== "undefined" && location.protocol === "https:" ? "; Secure" : "";
  const maxAge = Math.round(days * 24 * 60 * 60);
  document.cookie = `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=${maxAge}; SameSite=Lax${secure}`;
}

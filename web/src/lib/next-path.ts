/**
 * Powrót po logowaniu (`/login?next=...`): proxy dokleja `next`, gdy sesja wygasła na stronie, która ma sens
 * tylko po zalogowaniu (np. zgoda dla aplikacji OAuth). Dozwolone są wyłącznie ścieżki tego originu:
 * zaczynają się od `/`, nie od `//` ani `/\` (przeglądarka potraktowałaby je jak adres innej domeny).
 *
 * Link logowania przychodzi e-mailem i bywa otwierany w nowej karcie, więc `next` jest zapamiętywany
 * w `localStorage` na czas ważności linku i zużywany jednorazowo po udanym logowaniu.
 */
const STORAGE_KEY = "daas.login.next";
/** Tyle pamiętamy `next` (ważność linku logowania to domyślnie 15 min, maks. 120 min). */
const REMEMBER_MS = 2 * 60 * 60 * 1000;
const MAX_LENGTH = 2048;

/** Bezpieczna ścieżka powrotu albo null (wtedy obowiązuje domyślny cel po logowaniu). */
export function safeNextPath(value: string | null | undefined): string | null {
  if (!value || value.length > MAX_LENGTH) return null;
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return null;
  // znaki sterujące i ukośniki wsteczne: część przeglądarek normalizuje je do `/`
  if (/[\u0000-\u001f\u007f\\]/.test(value)) return null;
  if (value === "/login" || value.startsWith("/login?") || value.startsWith("/login/")) return null;
  return value;
}

export function rememberNextPath(value: string | null | undefined, now: number = Date.now()): void {
  const path = safeNextPath(value);
  if (!path) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ path, until: now + REMEMBER_MS }));
  } catch {
    // pamięć przeglądarki zablokowana: powrót działa wtedy tylko w tej samej karcie (parametr adresu)
  }
}

/** Odczytuje i usuwa zapamiętaną ścieżkę (jednorazowo); przeterminowana albo niepoprawna = null. */
export function takeRememberedNextPath(now: number = Date.now()): string | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    window.localStorage.removeItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const { path, until } = parsed as { path?: unknown; until?: unknown };
    if (typeof path !== "string" || typeof until !== "number" || until < now) return null;
    return safeNextPath(path);
  } catch {
    return null;
  }
}

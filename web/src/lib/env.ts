/**
 * Konfiguracja środowiskowa kokpitu.
 *
 * NEXT_PUBLIC_API_ORIGIN jest wstawiane do paczki w czasie budowania (build-time).
 * Ustaw je na pusty ciąg (`NEXT_PUBLIC_API_ORIGIN=`), jeśli API stoi za tym samym
 * originem (Caddy: /api/* -> api, reszta -> web). Wtedy wszystkie adresy są względne.
 */
const rawOrigin = process.env.NEXT_PUBLIC_API_ORIGIN ?? "http://127.0.0.1:8000";

/** Origin API bez końcowego ukośnika; pusty ciąg = ten sam origin co kokpit. */
export const API_ORIGIN: string = rawOrigin.replace(/\/+$/, "");

/** Prefiks wersji API (jedno miejsce). */
export const API_PREFIX = "/api/v1";

/** Pełny adres zasobu API (do fetch, EventSource i src ramki z raportem). */
export function apiUrl(path: string): string {
  return `${API_ORIGIN}${API_PREFIX}${path}`;
}

export const APP_NAME = "DaaS Engine Cloud";

/** Limity uploadu zgodne z SPEC 2.4 (CSV/XLSX <= 10 MiB, paczka JSON <= 5 MiB). */
export const MAX_TABLE_UPLOAD_BYTES = 10 * 1024 * 1024;
export const MAX_PACKAGE_UPLOAD_BYTES = 5 * 1024 * 1024;

export const CSRF_COOKIE = "daas_csrf";
export const CSRF_HEADER = "X-CSRF-Token";
export const LANG_COOKIE = "lang";
export const THEME_COOKIE = "theme";

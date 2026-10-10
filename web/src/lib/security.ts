/** Czyste funkcje bezpieczeństwa używane przez proxy (testowalne bez środowiska Next). */

export interface CspOptions {
  nonce: string;
  /** Origin API ("" = ten sam origin co kokpit). */
  apiOrigin: string;
  isDev?: boolean;
}

/**
 * CSP kokpitu: skrypty tylko z nonce + strict-dynamic, style tylko z 'self' (zero `style=""`),
 * połączenia i ramki do własnego originu i originu API, brak osadzania kokpitu w ramkach.
 */
export function buildCsp({ nonce, apiOrigin, isDev = false }: CspOptions): string {
  const api = apiOrigin ? ` ${apiOrigin}` : "";
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    // w dev Turbopack/HMR wstrzykuje style; w produkcji CSS jest wyłącznie plikiem
    `style-src 'self'${isDev ? " 'unsafe-inline'" : ""}`,
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src 'self'${api}${isDev ? " ws: wss:" : ""}`,
    `frame-src 'self'${api}`,
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "object-src 'none'",
  ];
  return directives.join("; ");
}

export function generateNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/** Ścieżki dostępne bez sesji. */
const PUBLIC_PATHS = ["/login", "/dpa"];

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));
}

export const SECURITY_HEADERS: Record<string, string> = {
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
};

/**
 * Nagłówki przekazywane przy serwerowym `GET /api/v1/me` z proxy kokpitu: bez nich `limit_ip` backendu
 * widzi jeden adres (kontener `web`) zamiast klientów. Pierwszy adres z `x-forwarded-for` żądania
 * (ustawia go Caddy), a gdy go brak, nic nie zgadujemy. Protokół: `x-forwarded-proto` żądania albo schemat adresu.
 */
export function forwardedHeaders(
  incoming: Pick<Headers, "get">,
  fallbackProtocol: string,
): Record<string, string> {
  const out: Record<string, string> = {};
  const client = incoming.get("x-forwarded-for")?.split(",")[0]?.trim();
  if (client) out["x-forwarded-for"] = client;
  const proto = incoming.get("x-forwarded-proto")?.split(",")[0]?.trim() || fallbackProtocol;
  out["x-forwarded-proto"] = proto.replace(/:$/, "");
  return out;
}

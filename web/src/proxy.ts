/**
 * Proxy Next.js 16 (dawniej middleware): CSP z nonce, nagłówki bezpieczeństwa,
 * wybór języka (?lang=) i przekierowanie na /login, gdy `GET /api/v1/me` zwraca 401.
 *
 * Next 16 przemianował konwencję `middleware.ts` na `proxy.ts` (stary plik działa, ale jest
 * oznaczony jako przestarzały); logika jest ta sama.
 */
import { NextResponse, type NextRequest } from "next/server";
import { API_ORIGIN, API_PREFIX, LANG_COOKIE } from "@/lib/env";
import { SECURITY_HEADERS, buildCsp, generateNonce, isPublicPath } from "@/lib/security";
import { normalizeLocale } from "@/i18n";

const SESSION_CHECK_TIMEOUT_MS = 3000;

/** Zwraca false tylko wtedy, gdy API jednoznacznie odpowiada 401. Awarie API nie wylogowują. */
async function sessionRejected(request: NextRequest): Promise<boolean> {
  const base = process.env.API_INTERNAL_ORIGIN || API_ORIGIN || request.nextUrl.origin;
  try {
    const response = await fetch(`${base.replace(/\/+$/, "")}${API_PREFIX}/me`, {
      headers: { cookie: request.headers.get("cookie") ?? "", accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(SESSION_CHECK_TIMEOUT_MS),
    });
    return response.status === 401;
  } catch {
    return false;
  }
}

export async function proxy(request: NextRequest) {
  const nonce = generateNonce();
  const csp = buildCsp({
    nonce,
    apiOrigin: API_ORIGIN,
    isDev: process.env.NODE_ENV === "development",
  });

  const langParam = request.nextUrl.searchParams.get("lang");
  const locale = normalizeLocale(langParam ?? request.cookies.get(LANG_COOKIE)?.value);

  const finish = (response: NextResponse): NextResponse => {
    response.headers.set("Content-Security-Policy", csp);
    for (const [name, value] of Object.entries(SECURITY_HEADERS)) response.headers.set(name, value);
    if (langParam) {
      response.cookies.set(LANG_COOKIE, locale, {
        path: "/",
        sameSite: "lax",
        maxAge: 60 * 60 * 24 * 365,
      });
    }
    return response;
  };

  if (!isPublicPath(request.nextUrl.pathname) && (await sessionRejected(request))) {
    const login = new URL("/login", request.url);
    return finish(NextResponse.redirect(login));
  }

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("x-daas-lang", locale);
  requestHeaders.set("Content-Security-Policy", csp);

  return finish(NextResponse.next({ request: { headers: requestHeaders } }));
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico|robots.txt|samples/).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};

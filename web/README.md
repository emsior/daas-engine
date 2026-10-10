# Kokpit DaaS Engine Cloud (`web/`)

Next.js 16 (App Router, React 19, TypeScript strict), Tailwind CSS 4, TanStack Query 5. Zero zewnętrznych CDN i fontów (fonty systemowe z tokenów). Interfejs po polsku (domyślnie) i angielsku, jasny/ciemny motyw, WCAG 2.2 AA.

## Uruchomienie

Wymagania: Node.js 22 LTS lub nowszy, działające API (`DAAS_MODE=cloud`, domyślnie `http://127.0.0.1:8000`).

```bash
cd web
npm ci
npm run dev          # http://localhost:3000
```

Produkcja (obraz `standalone`):

```bash
npm run build
cp -r public .next/standalone/public
mkdir -p .next/standalone/.next && cp -r .next/static .next/standalone/.next/static
PORT=3000 HOSTNAME=0.0.0.0 node .next/standalone/server.js
```

`next start` działa, ale ostrzega przy `output: "standalone"`; w obrazie Docker używaj `node .next/standalone/server.js`.

## Zmienne środowiskowe

| Zmienna                  | Kiedy czytana                           | Domyślnie                                           | Znaczenie                                                                                                                                                          |
| ------------------------ | --------------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `NEXT_PUBLIC_API_ORIGIN` | **build** (wstawiana do paczki) i proxy | `http://127.0.0.1:8000`                             | Origin API. Pusty (`NEXT_PUBLIC_API_ORIGIN=`) = ten sam origin co kokpit (Caddy: `/api/*` do API, reszta do kokpitu). Trafia do `connect-src` i `frame-src` w CSP. |
| `API_INTERNAL_ORIGIN`    | runtime (serwer)                        | `NEXT_PUBLIC_API_ORIGIN`, a gdy pusty adres kokpitu | Adres, pod którym serwer kokpitu pyta `GET /api/v1/me` (przekierowanie na `/login`). W compose np. `http://api:8000`.                                              |

Cookies: sesja `HttpOnly` i `daas_csrf` ustawia API. Kokpit i API muszą być w tej samej witrynie (ta sama domena, porty bez znaczenia), a po stronie API `DAAS_COCKPIT_ORIGIN` musi wskazywać origin kokpitu (CORS z `credentials`).

## Skrypty

| Skrypt                            | Działanie                                                                          |
| --------------------------------- | ---------------------------------------------------------------------------------- |
| `npm run dev` / `build` / `start` | Next.js                                                                            |
| `npm run lint`                    | ESLint (`eslint-config-next` + zakaz `any`, `style=""`, `dangerouslySetInnerHTML`) |
| `npm run typecheck`               | `tsc --noEmit`                                                                     |
| `npm test`                        | Vitest + React Testing Library (jsdom)                                             |
| `npm run e2e`                     | Playwright przeciw działającemu kokpitowi i backendowi                             |
| `npm run gen:api`                 | typy z `../docs/openapi.json` do `src/lib/api/schema.d.ts`                         |
| `npm run format`                  | Prettier                                                                           |

## Typy API: ręczne teraz, z `openapi.json` później

`src/lib/api/types.ts` zawiera typy napisane ręcznie według SPEC_v2 sekcja 2.4. Gdy w repo pojawi się `docs/openapi.json`:

1. `npm run gen:api` (tworzy `src/lib/api/schema.d.ts`; plik jest w `.prettierignore` i ignorowany przez ESLint).
2. W `types.ts` zastąp definicje aliasami, np. `export type Org = components["schemas"]["OrgOut"];` i popraw różnice (kształt `Me`, `Run`, `UploadPreview`, `EvidenceReport`, zdarzenia SSE).
3. Ścieżki zmienia się w jednym miejscu: obiekt `paths` w `src/lib/api/client.ts`.
4. `npm run typecheck` pokaże każde miejsce, w którym kontrakt się rozjechał.

## Struktura

```
src/app/           trasy (App Router): (auth)/login|onboarding, (app)/ kokpit, upload, runs/[id], evidence, settings/*, dpa
src/components/    komponenty współdzielone (opis: design/COMPONENTS.md); shell/ = nagłówek, nawigacja, bramka sesji
src/features/      logika ekranów: upload (reducer + kreator), run (weryfikator), evidence, settings
src/lib/           api/ (client, hooks, types), sse, format, hash, validation, security (CSP)
src/i18n/          pl.json, en.json, useT()
src/styles/        tokens/base/components/tailwind (Designer, bez zmian), app.css (uzupełnienia), globals.css (jedyny import)
src/proxy.ts       CSP z nonce, nagłówki bezpieczeństwa, ?lang=, przekierowanie na /login
e2e/               Playwright
```

## Bezpieczeństwo i dostępność (skrót)

- **CSP** (nonce, `strict-dynamic`, `style-src 'self'`, bez `unsafe-inline`) generuje `src/proxy.ts`. Next 16 przemianował `middleware.ts` na `proxy.ts`; logika jest ta sama. Wszystkie widoki są renderowane dynamicznie (nonce na żądanie).
- Brak `style=""`, brak `dangerouslySetInnerHTML` (reguła ESLint). Treść DPA jest renderowana z Markdown do JSX (`src/lib/markdown.tsx`).
- Raport jest w `<iframe sandbox="">` (bez `allow-scripts` i `allow-same-origin`).
- „Sprawdź sam”: SHA-256 liczone w przeglądarce (Web Crypto), zero żądań sieciowych (test e2e `verify-local-hash`). Wymaga HTTPS lub `localhost`.
- Fokus po zmianie trasy trafia na `h1`; jeden stały region `aria-live`; podsumowanie błędów z fokusem.

## Testy

`npm test` uruchamia testy jednostkowe. E2E (`npm run e2e`) wymaga:

- działającego kokpitu (`E2E_BASE_URL`, domyślnie `http://127.0.0.1:3000`),
- backendu w trybie `cloud` z adapterem e-mail `console`,
- dostępu do katalogu outboxa z linkami logowania (`E2E_OUTBOX_DIR`, domyślnie `../runtime/outbox`). Helper `e2e/helpers.ts` szuka w plikach fragmentu `token=...`; jeśli backend zapisuje link w innym formacie, popraw `readLatestLoginToken`.

Wersja `@playwright/test` jest przypięta do 1.56.1 (Chromium 1194 z `/opt/pw-browsers`). Nie uruchamiaj `playwright install`, jeśli przeglądarki są już w `PLAYWRIGHT_BROWSERS_PATH`.

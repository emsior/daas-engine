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

Proxy kokpitu przekazuje do serwerowego `GET /api/v1/me` pierwszy adres z `x-forwarded-for` oraz `x-forwarded-proto` przychodzącego żądania (ustawia je Caddy), żeby limit per IP w API liczył klientów, a nie kontener `web`.

Ramka z raportem: API wysyła raport z `frame-ancestors 'self'`, więc osadzenie działa tylko przy jednym originie kokpitu i API (produkcja za Caddy, albo lokalnie przez `e2e/proxy.mjs`). Przy `npm run dev` na osobnych portach przeglądarka zablokuje ramkę; „Otwórz raport” i pobieranie działają zawsze.

Cookies: sesja `HttpOnly` i `daas_csrf` ustawia API. Kokpit i API muszą być w tej samej witrynie (ta sama domena, porty bez znaczenia; lokalnie otwieraj kokpit pod `http://127.0.0.1:3000`, nie `localhost`, bo domyślny adres API to `127.0.0.1:8000`), a po stronie API `DAAS_COCKPIT_ORIGIN` musi wskazywać origin kokpitu (CORS z `credentials`).

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

## Typy API: z `docs/openapi.json`

`docs/openapi.json` jest eksportem z aplikacji (test świeżości w pytest). `npm run gen:api` generuje z niego `src/lib/api/schema.d.ts` (plik jest w repo, w `.prettierignore` i poza ESLintem; po zmianie API odśwież go tym skryptem i zacommituj razem z `openapi.json`).

- `src/lib/api/wire.ts`: aliasy `Wire*` do `components["schemas"][...]` i funkcje mapujące odpowiedzi backendu na typy widoków. Rozjazdy kontraktu (kształt `/me`, strony `{items}` zamiast tablic, `UploadOut`, `EvidenceOut.checks`, `UsageReport`, `WebhookPingResult`) są zamknięte w tym jednym pliku.
- `src/lib/api/types.ts`: typy widoków i żądań. Typy żądań są sprawdzane względem schematów backendu w `src/lib/api/contract.test.ts` (błąd kompilacji przy zmianie nazwy lub typu pola); ten sam test sprawdza, że każda ścieżka z `client.ts` istnieje w OpenAPI.
- Ścieżki zmienia się w jednym miejscu: obiekt `paths` w `src/lib/api/client.ts`.
- Kody odmowy uploadu (`problem+json`, pole `code`) mapuje `src/features/upload/uploadErrors.ts` na wzorce komunikatów (typ, rozmiar, pusty plik, brak kolumn, kodowanie/separator).

## Struktura

```
src/app/           trasy (App Router): (auth)/login|onboarding, (app)/ kokpit, upload, runs/[id], evidence, settings/*, dpa
src/components/    komponenty współdzielone (opis: design/COMPONENTS.md); shell/ = nagłówek, nawigacja, bramka sesji
src/features/      logika ekranów: upload (reducer + kreator), run (weryfikator), evidence, settings
src/lib/           api/ (client, hooks, types), sse, format, hash, validation, security (CSP)
src/i18n/          pl.json, en.json, useT()
src/styles/        tokens/base/components/tailwind (Designer, bez zmian), app.css (uzupełnienia), globals.css (jedyny import)
src/proxy.ts       CSP z nonce, nagłówki bezpieczeństwa, ?lang=, przekierowanie na /login
e2e/               Playwright (specy, helpers, proxy.mjs, stack.sh)
```

## Bezpieczeństwo i dostępność (skrót)

- **CSP** (nonce, `strict-dynamic`, `style-src 'self'`, bez `unsafe-inline`) generuje `src/proxy.ts`. Next 16 przemianował `middleware.ts` na `proxy.ts`; logika jest ta sama. Wszystkie widoki są renderowane dynamicznie (nonce na żądanie).
- Brak `style=""`, brak `dangerouslySetInnerHTML` (reguła ESLint). Treść DPA jest renderowana z Markdown do JSX (`src/lib/markdown.tsx`).
- Raport jest w `<iframe sandbox="">` (bez `allow-scripts` i `allow-same-origin`).
- „Sprawdź sam”: SHA-256 liczone w przeglądarce (Web Crypto), zero żądań sieciowych (test e2e `verify-local-hash`). Wymaga HTTPS lub `localhost`.
- Fokus po zmianie trasy trafia na `h1`; jeden stały region `aria-live`; podsumowanie błędów z fokusem.
- Logowanie linkiem: gdy link z maila otwarto w innej przeglądarce niż ta, w której podano adres, API odpowiada `409 confirm_required` (z zamaskowanym `email_hint`), a `/login` pokazuje ekran „Zaloguj w tej przeglądarce jako …?”. Przycisk ponawia `POST /auth/magic-link/verify` z `confirm: true`.
- Postęp zadania: SSE; gdy strumień ani razu się nie otworzy (np. 429 `sse_limit`), kokpit po 2 próbach przechodzi na `GET /jobs/{id}` co 3 s. „Anuluj” jest dostępne tylko dla zadania w kolejce (`queued`).
- Zarządzanie członkami (`/settings/members`): zaproszenia (lista, cofnięcie), zmiana roli (właściciel), usunięcie z potwierdzeniem; komunikat `last_owner`.

## Testy

`npm test` uruchamia testy jednostkowe (Vitest, jsdom). E2E (Playwright: `npm run e2e` = Chromium z `PLAYWRIGHT_BROWSERS_PATH`, projekty `mobile-375` i `desktop-1280`; `npm run e2e:firefox` = `desktop-firefox`; `npm run e2e:webkit` = `mobile-webkit`, 375 px) wymaga pełnego stosu w układzie produkcyjnym: jeden origin, `/api/*` do API, reszta do kokpitu. Raport jest osadzany w `<iframe>`, a API wysyła go z `frame-ancestors 'self'`, więc przy osobnych portach kokpitu i API ramka raportu jest blokowana; stąd proxy jednego originu.

```bash
cd web
export E2E_RUNTIME=/tmp/daas-e2e               # dane testowe (kasowane przy starcie)
./e2e/stack.sh start                            # backend cloud + e-mail console + kokpit (standalone) + proxy :3100
E2E_BASE_URL=http://127.0.0.1:3100 E2E_OUTBOX_DIR=$E2E_RUNTIME/outbox npm run e2e
E2E_BASE_URL=http://127.0.0.1:3100 E2E_OUTBOX_DIR=$E2E_RUNTIME/outbox npm run e2e:firefox
./e2e/stack.sh stop

# WebKit: tylko po https (po http odrzuca ciasteczka Secure i __Host-, które API wystawia zawsze)
E2E_TLS=1 ./e2e/stack.sh start                  # proxy :3100 po https, jednorazowy certyfikat self-signed w $E2E_RUNTIME/tls
E2E_BASE_URL=https://127.0.0.1:3100 E2E_OUTBOX_DIR=$E2E_RUNTIME/outbox npm run e2e:webkit
./e2e/stack.sh stop
```

`stack.sh` buduje kokpit z `NEXT_PUBLIC_API_ORIGIN=` (pusty = ten sam origin), jeśli nie ma `.next/standalone`; wymuś przebudowę usuwając `.next`. Wyłącza limity logowania (`DAAS_RATE_LIMIT_*=0`), bo seria testów loguje się z jednego adresu IP. Link logowania z adaptera `console` helper `e2e/helpers.ts` czyta z `E2E_OUTBOX_DIR` (pliki JSON z polem `link`).

Specy: `login-onboarding-upload-report`, `api-keys`, `verify-local-hash` (zero żądań sieciowych), `upload-progress-sse`, `upload-errors`, `evidence` (fixture `data/fixtures/uksc`), `members`, `settings`, `states` (loading/error/empty/success), `preferences` (PL/EN, motyw), `responsive` (375 px), `security` (CSP; naruszenia ze zdarzeń `securitypolicyviolation` w każdym silniku, zero zewnętrznych originów), `a11y` (axe w obu motywach, klawiatura, fokus). Raport axe do pliku: `A11Y_LOG=/ścieżka/a11y.tsv`.

Wersja `@playwright/test` jest przypięta do 1.56.1 (Chromium 1194 z `/opt/pw-browsers`; Firefox 1495 i WebKit 2215 dla `e2e:firefox`/`e2e:webkit`: `npx playwright install firefox webkit` i `npx playwright install-deps firefox webkit`). Nie uruchamiaj `playwright install`, jeśli przeglądarki są już w `PLAYWRIGHT_BROWSERS_PATH`. Konteksty tworzone w teście (`browser.newContext`) bierz z `newProjectContext` w `e2e/helpers.ts` (opcje projektu, w tym `ignoreHTTPSErrors`); schowek: `prepareClipboard`/`readClipboard` (Firefox i WebKit nie pozwalają czytać schowka).

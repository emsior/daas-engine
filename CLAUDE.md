@AGENTS.md

# CLAUDE.md — DaaS Engine (PPC)

> Kontekst, którego nie da się wywnioskować z kodu. Zasady dla agentów są w `AGENTS.md`
> (importowane w pierwszej linii). Historia zmian: `CHANGELOG.md`. Architektura: `docs/ARCHITECTURE.md`.

## Czym jest

**DaaS Engine** — silnik raportowy dla małych firm bez API. Klient wysyła eksport ze sprzedaży (CSV lub XLSX)
→ pipeline liczy metryki → klient dostaje gotowy raport HTML. Drugi wertykał: **kolektor artefaktów + raport statusu**
(pipeline `uksc_evidence`). To produkt komercyjny marki PPC; oferta i cennik są wyłącznie na https://prochpc.pl
(landing „Raport w Poniedziałek"). Każda zmiana ma odpowiadać na pytanie „czy to przybliża do sprzedaży",
nie „czy to ładniejszy kod".

Jeden kod, dwa tryby (`DAAS_MODE`): `selfhost` (jedna organizacja `default`, klucz z `DAAS_API_KEY`, n8n, trasy legacy)
i `cloud` (wiele organizacji, kokpit www, klucze API per organizacja, zadania asynchroniczne, limity planów).

## Status: 2.0.0 w przygotowaniu — gałąź `claude/saas-2026-10`

- `main` = v1.0.0 (tryb utrzymania od 28.09.2026). Prace nad 2.0.0 idą na gałęzi `claude/saas-2026-10`.
- Code freeze z v1.0.0 **uchylony poleceniem właściciela 09.10.2026** (`AGENTS.md`, zasada 8). Pozostałe zasady bez zmian.
- Zakres 2.0 (kontrakt: SPEC_v2 poza repo): hardening (klucz API na trasach, bind, policy, escapowanie raportów, narracja LLM opt-in),
  control plane + tenancy, API v1, kokpit `web/`, `deploy/`, CI (web, e2e, lighthouse, pip-audit), dokumentacja.
- Poza zakresem 2.0: płatności online, passkeys, SSO, wspólna baza z `tenant_id`, hostowane n8n, SLA.
- Push na `origin` to osobne polecenie właściciela. Odbiór bram (hardening, cloud, kokpit) robi reviewer; test „obcego" po instalacji na VPS.
- Wersja `2.0.0` jest w `pyproject.toml`, `app/core/config.py` i `web/package.json`; `/health.version` czyta metadane zainstalowanego pakietu.

## Stack

| Warstwa | Technologia |
|---|---|
| Język backendu | Python ≥ 3.11 — CI: 3.11, 3.12, 3.13; obraz Docker `3.12-slim`; lokalny dev 3.12/3.13 |
| API | FastAPI (async), jeden proces roboczy (`workers=1`) |
| Control plane | SQLAlchemy 2.0 async + Alembic (`alembic/versions/`), SQLite (`aiosqlite`) domyślnie, Postgres opcjonalnie |
| Data plane | DuckDB, plik per organizacja: `runtime/tenants/<org_id>/` |
| Przetwarzanie | pandas, openpyxl (import XLSX z limitami) |
| Orkiestracja (selfhost) | n8n (webhook + harmonogram; definicje w `n8n/`) |
| Kokpit `web/` | Next.js 16 (App Router), React 19, TypeScript strict, Tailwind 4, TanStack Query, zod; Vitest + Testing Library; Playwright (e2e) |
| Wdrożenie | Docker Compose (`deploy/`), Caddy (TLS), obrazy `api` (korzeń repo) i `web` (`web/Dockerfile`) |
| Zależności Pythona | `requirements.lock`, `requirements-dev.lock` (pip-tools, hashe SHA-256; `--require-hashes`); wpisy tylko-Windows/poza-Windows dopisuje `tools/lock_windows_pins.py` |
| Agenci AI (MCP) | `mcp` 2.3 (oficjalne SDK): `POST /mcp`, Streamable HTTP bezstanowy + JSON, klucz API jako Bearer, 13 narzędzi (w tym `upload_xlsx` — skoroszyt jako base64 — i `start_run` z okresem `period_from`/`period_to`) + 4 zasoby (szablony `daas://`, `resources/list` z 20 najnowszymi raportami) + 3 prompty w `app/mcp_server/`; OAuth 2.1 dla konektorów ChatGPT/Claude (serwer autoryzacji z SDK + `app/control/oauth.py`, zgoda w kokpicie `/oauth/consent`, tylko cloud); `docs/MCP.md` |
| Harmonogram | `app/control/schedule.py` (pętla w lifespanie, tylko cloud), `app/control/notifications.py` (mail i wiadomość na czacie po runie: Slack / Teams / JSON, payloady w `app/control/chat_messages.py`, wysyłka `post_json` z `webhooks.py` z ochroną SSRF), `GET/PUT /orgs/{id}/schedule` (adres webhooka czatu tylko do zapisu, w odpowiedzi maska), Alembic `0004`, `0005` |
| Testy | pytest — ok. 2090 przypadków (2086 passed + 1 skipped po scaleniu pięciu przyrostów P-12 13.10.2026, plus testy poprawek audytu Wave 2), pełny przebieg 8–10 min; Linux (3.13) zielone; Windows (3.12): 1777/0 przed P-12, pełny przebieg po P-12 w `bundle\pytest_windows_2026-10-13b.log` warsztatu; Vitest w `web/` (210) |
| CI | GitHub Actions: `test` (3.11, 3.12, 3.13), `security` (gitleaks, semgrep, zizmor, pip-audit), `web`, `e2e`, `lighthouse`, `docker`; Dependabot (pip, actions, docker, npm); akcje przypięte do SHA |
| CI poza SQLite i Chromium | `postgres`: PostgreSQL 16 z obrazu `ubuntu-24.04`, `tests/test_postgres.py` (migracje, `FOR UPDATE` przy dwóch sesjach, SAVEPOINT), `asyncpg` z `requirements-postgres.lock`; `e2e-browsers`: Firefox po http, WebKit po https (`E2E_TLS=1`) |

⚠ **Od 2.0 w repo jest JavaScript**: `web/` ma własny `package.json` i `package-lock.json`. Reszta repo (poza `web/`) to Python.

## Komendy

```bash
# backend (z korzenia repo)
. .venv/bin/activate                      # Windows: .\.venv\Scripts\Activate.ps1
pip install --require-hashes -r requirements-dev.lock && pip install --no-deps -e .   # działa też na Windows (uvloop za markerem sys_platform)
python -m pytest -p no:cacheprovider      # addopts=-q już jest: nie dodawaj drugiego -q (znika podsumowanie)
pip install --require-hashes -r requirements-postgres.lock   # asyncpg: testy na Postgresie i control plane na Postgresie
DAAS_TEST_POSTGRES_URL=postgresql+asyncpg://<rola z CREATEDB>:<hasło>@127.0.0.1:5432/postgres python -m pytest tests/test_postgres.py -p no:cacheprovider   # bez URL-a: 1 skipped
ruff check app tests                      # ta sama wersja co w CI (0.17.0)
python -m app.main                        # API na 127.0.0.1:8000; selfhost wymaga DAAS_API_KEY, cloud wymaga DAAS_SECRET_KEY
python -m app.cli migrate | migrate-runtime | create-org --name ... --owner-email ... | set-plan --org ... --plan free|pro|selfhost
python tools/export_openapi.py            # odświeża docs/openapi.json (test pilnuje aktualności)

# locki po zmianie zależności (pip-tools w venv na Pythonie 3.11 = minimum projektu, jak w nagłówku locków)
pip-compile --all-build-deps --allow-unsafe --generate-hashes --output-file=requirements.lock --strip-extras pyproject.toml
pip-compile --all-build-deps --allow-unsafe --extra=dev --generate-hashes --output-file=requirements-dev.lock --strip-extras pyproject.toml
python tools/lock_windows_pins.py         # wpisy z markerem sys_platform (uvloop, tzdata, pywin32, colorama) + hashe z PyPI; --check w CI/review

# kokpit
cd web && npm ci
npm run typecheck && npm run lint && npm test
npm run dev                               # http://localhost:3000, API pod NEXT_PUBLIC_API_ORIGIN (domyślnie 127.0.0.1:8000)
npm run gen:api                           # typy z ../docs/openapi.json

# e2e (Playwright, backend w trybie cloud z poczta `console`)
bash web/e2e/stack.sh start               # API + kokpit (standalone) + proxy jednego originu na :3100
cd web && E2E_BASE_URL=http://127.0.0.1:3100 E2E_OUTBOX_DIR=<E2E_RUNTIME>/outbox npm run e2e   # Chromium: mobile-375, desktop-1280
cd web && E2E_BASE_URL=http://127.0.0.1:3100 E2E_OUTBOX_DIR=<E2E_RUNTIME>/outbox npm run e2e:firefox
bash web/e2e/stack.sh stop
E2E_TLS=1 bash web/e2e/stack.sh start     # WebKit tylko po https: po http odrzuca ciasteczka Secure/__Host- (certyfikat jednorazowy)
cd web && E2E_BASE_URL=https://127.0.0.1:3100 E2E_OUTBOX_DIR=<E2E_RUNTIME>/outbox npm run e2e:webkit
bash web/e2e/stack.sh stop

# wdrożenie (VPS)
cd deploy && ./gen-env.sh --domain raporty.example.com && docker compose build && docker compose --profile edge up -d
./smoke.sh --public https://raporty.example.com --wait 90
```

## Przepływ i pipeline'y

```
cloud:    POST /api/v1/orgs/{id}/uploads → POST .../jobs (202) → GET .../jobs/{id}/events (SSE) → GET .../runs/{run_id}/report?fmt=html
selfhost: POST /upload → POST /run → GET /reports/{pipeline}/latest?fmt=html      (trasy legacy, tylko selfhost)
```

- `ecommerce_demo` — przychód, koszt, marża, tydzień-do-tygodnia (`weekly` + `wow`) z akcjami wyprowadzonymi z delt;
  alert n8n przy spadku przychodu ≥ 15% albo marży ≥ 3 p.p.
- `cs2_demo` — esports statistics (FACEIT API albo fixture).
- `uksc_evidence` — kolektor artefaktów + raport statusu UKSC/NIS2: kolektor PowerShell (read-only) → paczka JSON z hashami →
  raport „kontrola → wymóg → status → artefakt" z `package_sha256`. Nazwa pipeline'u jest częścią kontraktu API.

## Pułapki

| Problem | Obejście |
|---|---|
| Docker Desktop na Windows nie startuje (WSL / Hypervisor Platform) | uruchamiaj natywnie `URUCHOM_DAAS_PYTHON.ps1`; Docker dopiero pod self-host klienta |
| `.git/HEAD.lock`, `index.lock` zostają po gicie uruchamianym przez agenta w Windows | `GIT_OPTIONAL_LOCKS=0` dla komend odczytu; git wyłącznie z Windows (spod Linuksa pliki wyglądają na zmienione przez CRLF) |
| n8n 2.37 wymaga Node ≥ 24 | dotyczy tylko workflowów n8n, nie silnika ani kokpitu (Node 22 LTS) |
| Pełny `pytest` trwa ~2 min | agent uruchamia go w tle z `--junitxml` |
| `NEXT_PUBLIC_API_ORIGIN` jest wstawiane do paczki kokpitu w czasie budowania | zmiana wymaga ponownego `npm run build` / `docker compose build web`; pusty = ten sam origin (Caddy) |
| Kokpit i API muszą być w jednej witrynie (cookie sesji `SameSite=Lax`, CSRF) | w dev/e2e użyj proxy jednego originu `web/e2e/proxy.mjs` albo `DAAS_COCKPIT_ORIGIN` + `DAAS_COOKIE_SECURE=false` po http |
| Limity żądań i kwoty są w pamięci procesu | jeden worker; `WEB_CONCURRENCY`/`UVICORN_WORKERS` > 1 blokuje start. Za proxy adres klienta to adres proxy, dopóki `FORWARDED_ALLOW_IPS` go nie obejmie |
| Zmiana `DAAS_SECRET_KEY` | wylogowuje wszystkich i unieważnia sekrety podpisu webhooków; kopia danych go nie zawiera |
| Przeglądarki Playwright są już w `PLAYWRIGHT_BROWSERS_PATH` (Chromium) | nie uruchamiaj `playwright install` tam, gdzie są; wersja `@playwright/test` jest przypięta. Firefox i WebKit (tylko `e2e:firefox`/`e2e:webkit`): `npx playwright install firefox webkit` + `npx playwright install-deps firefox webkit` |
| WebKit odrzuca ciasteczka `Secure`/`__Host-` po http (Chromium i Firefox na 127.0.0.1 je przyjmują) | e2e w WebKicie tylko ze stosem `E2E_TLS=1` i `E2E_BASE_URL=https://…`; nie wyłączaj `DAAS_COOKIE_SECURE` dla testów |
| Migracje control plane idą tylko do przodu i wykonują się przy starcie | przed aktualizacją `deploy/backup.sh`; wycofanie wersji po migracji = `deploy/restore.sh` |
| Next 16 przemianował `middleware.ts` na `proxy.ts` | CSP z nonce i przekierowanie na `/login` są w `web/src/proxy.ts` |
| Obraz `api` nie widzi `docs/`, `web/`, `tests/` (`.dockerignore`) | `COPY` w `Dockerfile` bierze tylko `app/`, `data/`, `alembic/` i pliki projektu |
| Przeglądarkowe testy `web/` i `next build` dzielą katalog `web/.next` | nie uruchamiaj ich równolegle |
| `pip-compile` na Linuksie gubi markery środowiska (pakiety tylko-Windows znikają, `uvloop` traci `sys_platform != "win32"`) | po każdym `pip-compile` uruchom `python tools/lock_windows_pins.py`; locki kompiluj na Pythonie 3.11 (minimum projektu) |
| SDK `mcp`: `session_manager.run()` da się wejść tylko raz | `create_app` buduje nowy serwer MCP dla każdej aplikacji (`app.state.mcp`); lifespan wchodzi w `run()` tylko przy `DAAS_MCP_ENABLED=true` |
| Strefy czasowe harmonogramu (`zoneinfo`) | Linux bierze bazę IANA z systemu (`/usr/share/zoneinfo`; obraz `python:3.12-slim` ma pakiet `tzdata`), Windows z pakietu `tzdata` w locku (marker `sys_platform == "win32"`). Bez bazy `available_timezones()` jest puste i każdy `PUT .../schedule` dostaje `422` na `timezone` — test `test_timezone_validation_uses_the_iana_database` to wykrywa. Nie usuwaj `tzdata` z obrazu ani z locka |
| Windows: otwartego pliku nie da się usunąć (błąd współdzielenia, WinError 32), a bity trybu 0600 nic nie znaczą | pliki tymczasowe uploadu usuwa `remove_quietly` (ponowienia, reszta dla `cleanup_stale_tmp`); po timeoucie XLSX wątek dostaje `XLSX_CANCEL_GRACE_SEC` na zamknięcie pliku; pliki z sekretami tworzy `app/core/private_files.open_private` (DACL przez `icacls`). Podproces w testach startuje ze środowiskiem z `tests/support.minimal_subprocess_env` (`SYSTEMROOT` itd.) |

## Co jest zrobione (nie buduj tego drugi raz)

- Pipeline `upload → run → report` z mapperem PL, CSV i XLSX, metryki tydzień-do-tygodnia, alert n8n.
- Pipeline `uksc_evidence` z kolektorem `tools/uksc-collector.ps1` i schematem `schemas/uksc_collector_v1.json`.
- Hardening 2.0: klucz API, bind na loopback, policy domyślnie włączona, limity, escapowanie raportów, narracja LLM opt-in, lockfile z hashami.
- Control plane, tenancy, API v1 (organizacje, członkowie, klucze, uploady, zadania, SSE, raporty, verify, evidence, usage, audit, webhooki, metrics).
- Harmonogram i powiadomienia (12.10.2026): `GET/PUT /orgs/{id}/schedule`, tabela `org_schedules` (Alembic `0004`), cotygodniowe
  przypomnienie o eksporcie (`app/control/schedule.py`, pętla `SchedulerLoop` w lifespanie, tylko cloud, `DAAS_SCHEDULER_*`),
  auto-uruchomienie po uploadzie (`queue_job` wspólne z `POST /jobs`, pole `auto_job_id`), e-mail po runie
  (`app/control/notifications.py`, listener `run.finished`), sekcja w Ustawieniach kokpitu. Powiadomienie na czacie
  (13.10.2026): `notify_webhook_kind`/`notify_webhook_url` (Slack Incoming Webhook, Teams Workflows, dowolny JSON; adres tylko
  do zapisu — pominięty = bez zmian, `null` = usuń, podobnie rodzaj; maska w `notify_webhook_hint`), payloady w
  `app/control/chat_messages.py`, wysyłka `post_json` z SSRF i pinowaniem IP, audyt `schedule.webhook_notified`.
- Pulpit zdrowia danych (13.10.2026): `GET /orgs/{id}/data-health?weeks=` (`runs:read`; tygodnie ISO w strefie
  harmonogramu, podsumowanie, problemy z linkami do kokpitu; liczone w locie z control plane, bez audytu,
  `app/api/v1/data_health.py`), ekran `/health` w kokpicie (werdykt, wskaźniki, tabela 8/12/26 tygodni).
- OAuth 2.1 dla konektorów MCP (13.10.2026, tylko cloud): serwer autoryzacji z SDK `mcp` (`app/control/oauth.py`: `/authorize`,
  `/token`, `/register`, `/revoke`, metadane RFC 8414/9728; tabele `oauth_clients|codes|tokens`, Alembic `0005`), zgoda w kokpicie
  (`/oauth/consent`, `app/api/v1/oauth.py`), token `daas_oat_` w `/mcp` jak klucz organizacji zgody obcięty do bieżącej roli,
  granty `GET/DELETE /orgs/{id}/oauth/grants` + ekran „Połączone aplikacje”, retencja kodów/tokenów/porzuconych klientów.
- Kokpit `web/` (11 ekranów: kokpit, upload, zdrowie danych, run, evidence, ustawienia ×4, zgoda OAuth, DPA; PL/EN, jasny/ciemny motyw),
  `deploy/` (compose, Caddy, backup, restore, smoke), `docs/API.md`, `docs/SECURITY.md`.
- Serwer MCP pod `/mcp` (13 narzędzi, w tym `upload_xlsx` jako base64; te same funkcje serwisowe co API v1, audyt `mcp.tool`), `docs/MCP.md`; zasoby
  (`app/mcp_server/resources.py`: raport MD/JSON, status artefaktów, podsumowanie uploadu; audyt `mcp.resource`) i prompty
  (`app/mcp_server/prompts.py`: `weekly_summary`, `compare_runs`, `data_quality_check`; audyt `mcp.prompt`).
- Zakres dat runu (13.10.2026): `period_from`/`period_to` w `POST /jobs` i MCP `start_run` (kolumny `Job.period_*`, Alembic `0005`);
  runner filtruje rekordy `ecommerce_demo` po dacie zamówienia przed zapisem i metrykami (rekordy bez daty liczone osobno w `detail`),
  inne pipeline'y: `422 period_not_supported`; granice (od 2000-01-01, maks. 400 dni) wspólne z selfhost `POST /run` (`app/core/models.py`).
- DevSecOps: ruff, gitleaks, semgrep, zizmor, pip-audit, Dependabot, pre-commit, kontenery bez roota.
- README ze screenshotem i wideo demo `docs/daas_demo.mp4`; materiały pilota w `docs/pilot/`.
- Poza repo: landing ofertowy na prochpc.pl i wzór umowy powierzenia danych (RODO art. 28); w repo szkic `docs/DPA_v1_PL.md` do przeglądu prawnego.
- Naprawy po teście obcego (14.10.2026): parser dat per-komórka + ostrzeżenie o mieszanych formatach w `warnings[]`, SHA-256 raportu w selfhost REST (`/run`, `/reports/...`), lokalizacja raportu PL przy `locale=pl`, `app.cli set-plan --org --plan` (plus `PATCH /orgs` odrzuca nieznany `plan`); worked-example `uksc_evidence` i reszta napraw dokumentacji w `CHANGELOG.md` (sekcja „Fixes after the stranger test").

*Aktualizacja: przy każdej zmianie stacku, zasad albo statusu.*

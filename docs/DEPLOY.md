# Wdrożenie i konfiguracja

Skrót instrukcji z [`deploy/README.md`](../deploy/README.md) oraz pełna lista zmiennych środowiskowych. Dwa tryby uruchomienia:

| Tryb | Co to jest | Jak uruchomić |
|---|---|---|
| `selfhost` | jedna organizacja (`default`), klucz z `DAAS_API_KEY`, trasy API w katalogu głównym (`/upload`, `/run`), n8n | `docker-compose.yml` w katalogu głównym albo `pip install -e .` |
| `cloud` | wiele organizacji, kokpit www, klucze API per organizacja, zadania asynchroniczne, limity planów | `deploy/compose.yml` na VPS (poniżej) |

## Cloud na VPS: kroki

Wymagania: Docker Engine 25+ z Compose v2, VPS 2 vCPU / 4 GB RAM, domena z rekordem A/AAAA na serwer, otwarte porty 80 i 443.

```bash
git clone https://github.com/emsior/daas-engine.git /opt/daas-engine && cd /opt/daas-engine/deploy
./gen-env.sh --domain raporty.example.com          # tworzy .env (0600) z losowymi sekretami; nie nadpisuje istniejącego
$EDITOR .env                                       # DAAS_EMAIL_BACKEND=smtp + dane SMTP; usuń DAAS_ALLOW_CONSOLE_MAIL
docker compose build
docker compose --profile edge up -d                # z Caddy (TLS); bez profilu: tylko api i web
docker compose exec api python -m app.cli create-org --name "Nazwa firmy" --owner-email wlasciciel@example.com
./smoke.sh --public https://raporty.example.com --wait 90 --expect-version 2.0.0
```

`create-org` wypisuje (raz) `org_id` nowej organizacji oraz jej pierwszy, bootstrapowy `api_key` (prefiks + pełny klucz). Zanotuj je: tego klucza używasz do pierwszych wywołań `/api/v1` i `/mcp` albo do utworzenia kolejnych kluczy (`POST /api/v1/orgs/{org_id}/api-keys`); kolejne klucze tworzysz też w kokpicie. Właściciel i tak loguje się linkiem z e-maila — klucz jest do integracji maszynowych.

Aktualizacja: `./backup.sh`, nowy tag w `DAAS_VERSION`, `docker compose build`, `docker compose --profile edge up -d`, `./smoke.sh`.
Rollback: poprzedni `DAAS_VERSION` i `docker compose up -d` (obraz jest lokalnie); gdy nowa wersja zmigrowała bazę, dodatkowo `./restore.sh backups/<kopia>.tar.gz`.
Szczegóły, wspólne proxy z drugą aplikacją i kopie zapasowe: [`deploy/README.md`](../deploy/README.md).

## Układ danych

| Wolumen (w kontenerze) | Zawartość |
|---|---|
| `control` (`/data/control`) | `control.db`: organizacje, użytkownicy, klucze (tylko skróty SHA-256), zadania, ślad runów, audyt, webhooki |
| `runtime` (`/data/runtime`) | `tenants/<org_id>/`: `daas.duckdb`, `uploads/`, `reports/`, `audit.jsonl`; `outbox/` (linki logowania trybu `console`, tylko próby) |

Kopia zapasowa obejmuje oba wolumeny. Sekretów z `.env` nie obejmuje.

## Zmienne środowiskowe aplikacji

Źródłem prawdy jest `app/core/config.py` (`Settings`). Nazwy są niewrażliwe na wielkość liter. Szablon: [`.env.example`](../.env.example).
Kolumna „Tryb": `c` = istotne w `cloud`, `s` = w `selfhost`, `cs` = w obu.

### Aplikacja

| Zmienna | Domyślnie | Tryb | Znaczenie |
|---|---|---|---|
| `APP_ENV` | `prod` | cs | `dev` włącza `/docs`, `/redoc`, `/openapi.json`, auto-reload i dopuszcza webhooki na adresy niepubliczne; każda inna wartość je wyłącza |
| `APP_NAME` | `DaaS Engine` | cs | nazwa w OpenAPI i `/health` |
| `APP_VERSION` | `2.0.0` | cs | wersja awaryjna; `/health` podaje wersję zainstalowanego pakietu |
| `APP_HOST` | `127.0.0.1` | cs | adres nasłuchu; obraz Docker ustawia `0.0.0.0`. W `selfhost` adres inny niż loopback wymaga `DAAS_API_KEY` |
| `APP_PORT` | `8000` | cs | port nasłuchu |
| `LOG_LEVEL` | `INFO` | cs | poziom dziennika JSON |
| `DAAS_HOME` | katalog projektu | cs | katalog bazowy ścieżek względnych (`alembic/`, `data/fixtures`); obraz ustawia `/srv/app` |
| `DAAS_POLICY_ENFORCE` | `true` | cs | warstwa policy: granica katalogu roboczego i audyt, odmowa kończy się błędem |
| `DAAS_LLM_NARRATIVE` | `false` | s | zgoda na streszczenie przez zewnętrznego dostawcę LLM; sam `ANTHROPIC_API_KEY` niczego nie włącza. W `cloud` decyduje opcja organizacji `llm_narrative` (`PATCH /api/v1/orgs/{id}`), domyślnie wyłączona |

### Tryb, control plane, sesje

| Zmienna | Domyślnie | Tryb | Znaczenie |
|---|---|---|---|
| `DAAS_MODE` | `selfhost` | cs | `selfhost` albo `cloud`; `deploy/compose.yml` ustawia `cloud` |
| `DAAS_API_KEY` | brak | s | klucz instancji w nagłówku `X-API-Key` (min. 16 znaków). W `cloud` nie jest używany |
| `DAAS_SECRET_KEY` | brak | c | **wymagany w `cloud`**, min. 32 znaki. Podpis cookie sesji i sekretów webhooków. `openssl rand -hex 32` |
| `DAAS_CONTROL_DB_URL` | `sqlite+aiosqlite:///runtime/control.db` | cs | baza control plane (SQLAlchemy async). Postgres: `postgresql+asyncpg://...` i pakiet `asyncpg` (dodatek `postgres`; z hashami: `pip install --require-hashes -r requirements-postgres.lock`; obraz `api` go nie zawiera) |
| `DAAS_AUTO_MIGRATE` | `true` | cs | `alembic upgrade head` przy starcie (idempotentne); `false`, gdy migracje wykonuje operator (`python -m app.cli migrate`) |
| `DAAS_COCKPIT_ORIGIN` | brak | c | jedyny origin kokpitu dopuszczony przez CORS z cookie, np. `https://raporty.example.com`. Brak = CORS wyłączony |
| `DAAS_COOKIE_SECURE` | `true` | c | atrybut `Secure` cookie sesji; `false` tylko do lokalnych prób po http |
| `DAAS_SESSION_TTL_HOURS` | `336` | c | ważność sesji (14 dni) |
| `DAAS_MAGIC_LINK_TTL_MINUTES` | `15` | c | ważność linku logowania (1-120) |
| `CORS_ORIGINS` | `[]` | cs | lista JSON originów dla CORS bez cookie, np. `["https://example.com"]` |

### Poczta (link logowania, zaproszenia)

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `DAAS_EMAIL_BACKEND` | `console` | `console`: link w `runtime/outbox/` (plik 0600), poza `APP_ENV=prod` także w dzienniku (tylko próby i testy e2e); `smtp`: wysyłka stdlib `smtplib`. W `cloud` z `APP_ENV=prod` samo `console` **blokuje start** (patrz niżej) |
| `DAAS_ALLOW_CONSOLE_MAIL` | `false` | jawna zgoda na `console` w `cloud` z `APP_ENV=prod` (próby, pilotaż bez SMTP). `deploy/gen-env.sh` ustawia `true` razem z `console`; przy `smtp` usuń |
| `DAAS_EMAIL_FROM` | `DaaS Engine <no-reply@localhost>` | nadawca |
| `DAAS_SMTP_HOST` | brak | serwer SMTP |
| `DAAS_SMTP_PORT` | `587` | port |
| `DAAS_SMTP_USER` | brak | login (pusty = bez logowania) |
| `DAAS_SMTP_PASSWORD` | brak | hasło; nie trafia do dziennika |
| `DAAS_SMTP_STARTTLS` | `true` | STARTTLS po połączeniu |

### Limity i zadania

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `DAAS_RATE_LIMIT_RUN_PER_MIN` | `30` | uruchomienia (`/run`, `POST .../jobs`) na minutę, per klucz; `0` = wyłączone |
| `DAAS_RATE_LIMIT_UPLOAD_PER_MIN` | `30` | uploady na minutę, per klucz |
| `DAAS_RATE_LIMIT_IP_PER_MIN` | `300` | wszystkie trasy chronione, per adres IP |
| `DAAS_RATE_LIMIT_AUTH_FAIL_PER_MIN` | `10` | nieudane uwierzytelnienia, per adres IP |
| `DAAS_RATE_LIMIT_MAGIC_EMAIL_PER_MIN` | `3` | `POST /auth/magic-link`, per adres e-mail |
| `DAAS_RATE_LIMIT_MAGIC_IP_PER_MIN` | `10` | `POST /auth/magic-link`, per IP |
| `DAAS_UPLOAD_QUOTA_BYTES` | `1073741824` | łączna kwota plików w `uploads/` organizacji (1 GiB); `0` = bez limitu. Przekroczenie: `413` |
| `DAAS_MAX_CONCURRENT_RUNS` | `2` | równoległe runy w całym procesie (w obrębie organizacji zawsze 1); 1-64 |
| `DAAS_MAX_ACTIVE_TENANTS` | `16` | ile organizacji ma jednocześnie otwarty runner DuckDB (LRU); 1-1024. To limit **liczby** otwartych tenantów, nie pamięci: RAM ogranicza `mem_limit` kontenera |
| `DAAS_RETENTION_DAYS` | `90` | domyślna retencja nowej organizacji (dni; 1-3650), zmienna per organizacja (`PATCH /api/v1/orgs/{id}`). Uploady kasuje się po `retention_until` (wyliczonym przy wgraniu), pliki raportów i wiersze DuckDB runów starszych niż `retention_days` |
| `DAAS_RETENTION_INTERVAL_MIN` | `60` | co ile minut zadanie w tle kasuje dane po terminie (tylko `cloud`); `0` = wyłączone, 0-10080. Ręcznie: `python -m app.cli purge [--dry-run] [--org ID]`. Kopii zapasowych nie dotyczy |
| `DAAS_SCHEDULER_ENABLED` | `true` | pętla harmonogramu (cotygodniowe przypomnienia o eksporcie) w procesie API; tylko `cloud`. `false` wyłącza wysyłkę przypomnień w tym wdrożeniu (auto-uruchamianie po uploadzie i e-mail po runie działają dalej, jeśli organizacja je włączyła) |
| `DAAS_SCHEDULER_TICK_SEC` | `60` | co ile sekund pętla sprawdza terminy przypomnień (5-3600). Pętla działa w jednym procesie roboczym, jak reszta 2.0; zaległe przypomnienie po restarcie wychodzi raz |
| `DAAS_WEBHOOK_BACKOFF_SEC` | `1,5,25` | pauzy między ponowieniami dostawy webhooka (sekundy, po jednej próbie początkowej). Prób jest najwyżej 3, więc używane są tylko dwie pierwsze pauzy (`1` i `5`); trzecia (`25`) nigdy nie działa |
| `DAAS_NOTIFY_WEBHOOK_TIMEOUT_SEC` | `10` | limit czasu (s, 1-60) jednej wiadomości na czat zespołu po runie (Slack / Teams / JSON; adres ustawia organizacja w harmonogramie). Jedna próba, bez ponowień |
| `DAAS_SSE_HEARTBEAT_SEC` | `15` | odstęp komentarza `: heartbeat` w strumieniu SSE |
| `DAAS_SSE_MAX_PER_PRINCIPAL` | `5` | otwarte strumienie SSE na klucz lub sesję (1-100); powyżej: `429 sse_limit` |
| `DAAS_SSE_MAX_TOTAL` | `100` | otwarte strumienie SSE w całym procesie (1-10000) |
| `DAAS_SSE_MAX_SECONDS` | `1800` | maksymalny czas życia jednego strumienia (1-86400 s); klient łączy się ponownie |
| `DAAS_MCP_ENABLED` | `true` | serwer MCP dla agentów AI pod `POST /mcp` ([`MCP.md`](MCP.md)); `false` = trasy nie ma (`404`) |
| `DAAS_MCP_MAX_BODY_BYTES` | `3145728` | limit ciała żądania `/mcp` (3 MiB); większe: `413` (Caddy przepuszcza do 4 MiB) |
| `DAAS_MCP_MAX_UPLOAD_BYTES` | `2097152` | limit treści CSV narzędzia `upload_csv` (2 MiB); mniejszy niż limit ciała |
| `DAAS_RATE_LIMIT_MCP_PER_MIN` | `240` | żądania `/mcp` na minutę per klucz API (dowolna metoda JSON-RPC, po uwierzytelnieniu); powyżej `429` z `Retry-After`; `0` = bez limitu. Limit per adres IP (`DAAS_RATE_LIMIT_IP_PER_MIN`) obowiązuje niezależnie |
| `DAAS_OAUTH_ENABLED` | `true` | (tylko `cloud`) serwer autoryzacji OAuth 2.1 dla konektorów MCP (ChatGPT, Claude): `/authorize`, `/token`, `/register`, `/revoke`, `/.well-known/oauth-*` ([`MCP.md`](MCP.md#connect-from-chatgpt--claude-oauth)); `false` = tych tras nie ma (`404`), klucze API działają dalej |
| `DAAS_OAUTH_ISSUER_URL` | `DAAS_COCKPIT_ORIGIN` | publiczny origin wdrożenia (issuer), np. `https://raporty.example.com`, bez ścieżki; `https` (http tylko dla `localhost`). Bez niego i bez `DAAS_COCKPIT_ORIGIN` OAuth jest wyłączony (ostrzeżenie w dzienniku). Kokpit i API muszą być pod tym originem: Caddy kieruje trasy OAuth do API |
| `DAAS_OAUTH_ACCESS_TOKEN_TTL_SEC` | `3600` | ważność tokenu dostępu konektora (60-86400 s) |
| `DAAS_OAUTH_REFRESH_TOKEN_TTL_SEC` | `2592000` | ważność tokenu odświeżania (30 dni; liczona od nowa przy każdej rotacji; 3600 s - 365 dni) |
| `DAAS_OAUTH_AUTHORIZATION_TTL_SEC` | `600` | czas na decyzję na ekranie zgody od `/authorize` (60-3600 s); kod po zgodzie żyje 60 s |

Liczniki limitów są w pamięci procesu; aplikacja działa w jednym procesie roboczym (`WEB_CONCURRENCY` lub `UVICORN_WORKERS` powyżej 1 blokuje start).

### Obserwowalność

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `DAAS_METRICS_TOKEN` | brak | token `Authorization: Bearer` dla `GET /metrics`; bez niego trasa odpowiada `404` |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | (uvicorn) adresy proxy, którym wolno ustawiać `X-Forwarded-For`; `deploy/compose.yml` ustawia `*` (zmienna wdrożenia `DAAS_FORWARDED_ALLOW_IPS`), bo API jest osiągalne tylko z sieci compose i z loopbacku, a Caddy nadpisuje nagłówek. Ze wspólnym proxy wpisz jego adres |

### Magazyn danych

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `DUCKDB_PATH` | `runtime/daas.duckdb` | plik DuckDB trybu `selfhost`; katalog nadrzędny to katalog runtime (`tenants/`, `outbox/`). W `compose`: `/data/runtime/daas.duckdb` |
| `REPORTS_DIR` | `runtime/reports` | raporty trybu `selfhost` |
| `FIXTURES_DIR` | `data/fixtures` | dane przykładowe (zapasowe źródła pipeline'ów) |
| `SOURCE_TIMEOUT_SEC` | `10` | limit czasu zapytań do źródeł zewnętrznych |

### Źródła zewnętrzne (opcjonalne)

Brak klucza nie jest błędem: pipeline używa danych przykładowych i oznacza wynik `MOCK_DATA`.

| Zmienna | Znaczenie |
|---|---|
| `FACEIT_API_KEY`, `FACEIT_PLAYER_NICKNAME` | pipeline `cs2_demo` (esports statistics) |
| `LIQUIPEDIA_USER_AGENT` | zarezerwowane |
| `APIFY_TOKEN` | źródło danych `ecommerce_demo` |
| `DISCORD_WEBHOOK_URL` | powiadomienia po runie (`notify=true`, tryb `selfhost`) |
| `ANTHROPIC_API_KEY` | streszczenie LLM; działa tylko razem ze zgodą: `DAAS_LLM_NARRATIVE=true` (`selfhost`) albo opcją organizacji `llm_narrative` (`cloud`) |

### Zmienne wdrożenia (`deploy/.env`, nie `Settings`)

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `DAAS_VERSION` | z `pyproject.toml` | tag obrazów `api` i `web`; podbijaj przed `build`, wracaj do poprzedniego przy rollbacku |
| `DAAS_DOMAIN` | `localhost` | domena Caddy i domyślny `DAAS_COCKPIT_ORIGIN` |
| `NEXT_PUBLIC_API_ORIGIN` | pusty | origin API zapisany w paczce kokpitu **w czasie budowania**; pusty = ten sam origin (Caddy). Zmiana wymaga `docker compose build web` |
| `DAAS_FORWARDED_ALLOW_IPS` | `*` | wartość `FORWARDED_ALLOW_IPS` dla `api` (adresy proxy, którym wolno ustawiać `X-Forwarded-For`); ze wspólnym proxy adres proxy, nie `*` |
| `COMPOSE_FILE` | brak | `compose.yml:compose.llm.yml` włącza nakładkę ze streszczeniem LLM (przekazuje `ANTHROPIC_API_KEY`, ustawia `DAAS_LLM_NARRATIVE=true`) |
| `DAAS_API_IMAGE`, `DAAS_WEB_IMAGE` | `daas-engine-api`, `daas-engine-web` | nazwy obrazów (np. własny rejestr) |
| `DAAS_API_PORT`, `DAAS_WEB_PORT` | `8000`, `3000` | porty na `127.0.0.1` hosta |
| `BACKUP_DIR`, `KEEP`, `BACKUP_PREFIX` | `deploy/backups`, `14`, `daas` | parametry `backup.sh` |

`deploy/compose.yml` nie używa `env_file`: kontener `api` dostaje wyłącznie zmienne wymienione w jego sekcji `environment` (z `.env`, gdy ustawione). Zmienne źródeł i powiadomień trybu `selfhost` (`FACEIT_*`, `APIFY_*`, `ECOMMERCE_CSV_PATH`, `DISCORD_WEBHOOK_URL`, `DAAS_API_KEY`) i `ANTHROPIC_API_KEY` tam nie trafiają; nową zmienną aplikacji trzeba dopisać do `compose.yml`.

Kokpit czyta w czasie działania jeszcze `API_INTERNAL_ORIGIN` (adres API w sieci compose, `http://api:8000`), którym pyta o sesję przy każdej nawigacji.

## Bezpieczeństwo wdrożenia w skrócie

- Porty aplikacji tylko na `127.0.0.1`; TLS i nagłówki daje Caddy albo wspólne proxy hosta.
- Sekrety wyłącznie w `.env` (0600, poza gitem), klucze API i tokeny w bazie jako skróty SHA-256.
- `DAAS_EMAIL_BACKEND=smtp` na produkcji; `console` zapisuje linki logowania na dysku (`runtime/outbox/`), a API z `APP_ENV=prod` odmawia z nim startu bez `DAAS_ALLOW_CONSOLE_MAIL=true`.
- Kontenery `api` i `web` mają system plików tylko do odczytu (zapis: wolumeny i `tmpfs`), `cap_drop: ALL` i `no-new-privileges`; dziennik Caddy nie zawiera `?token=`, `Cookie` ani kluczy.
- Metryki tylko na loopbacku z tokenem; Caddy zwraca `404` dla `/metrics`.
- OAuth dla konektorów MCP: `deploy/Caddyfile` kieruje `/authorize`, `/token`, `/register`, `/revoke` i `/.well-known/oauth-*` do API (limit ciała 64 KiB), resztę ścieżek do kokpitu (ekran zgody `/oauth/consent`). Ze wspólnym proxy hosta przekaż te trasy do API tak samo jak `/api/*` i `/mcp`; issuer (`DAAS_OAUTH_ISSUER_URL` albo `DAAS_COCKPIT_ORIGIN`) musi być adresem, pod którym konektor widzi wdrożenie.
- Model zagrożeń i zgłaszanie podatności: [`SECURITY.md`](SECURITY.md).

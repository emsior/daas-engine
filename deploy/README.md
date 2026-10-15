# Wdrożenie DaaS Engine Cloud na VPS (Docker Compose)

Zestaw w tym katalogu uruchamia tryb `cloud` (wiele organizacji, kokpit www, klucze API, zadania asynchroniczne) na jednym serwerze. Tryb `selfhost` (jedna organizacja, klucz z `DAAS_API_KEY`, n8n) uruchamia `docker-compose.yml` w katalogu głównym repozytorium; ten plik go nie dotyczy.

| Plik | Rola |
|---|---|
| `compose.yml` | usługi `api`, `web`, `caddy` (profil `edge`), `maint` (profil `tools`, dla kopii zapasowych) |
| `Caddyfile` | TLS, nagłówki bezpieczeństwa, limit ciała 12 MiB dla `/api/*`, dziennik dostępu w JSON |
| `gen-env.sh` | tworzy `.env` z losowymi sekretami; nie nadpisuje istniejącego pliku |
| `backup.sh`, `restore.sh` | kopia (tar + SHA-256 + retencja) i odtworzenie danych |
| `smoke.sh` | test dymny po wdrożeniu |

Pilot na jednym VPS (przygotowanie hosta, DNS i poczta, kopie poza serwerem, monitoring, incydent, zakończenie): [`RUNBOOK.md`](RUNBOOK.md).

```
internet ──443──► caddy ──/api/*──► api :8000 ──► wolumeny: control (control.db), runtime (organizacje)
                    └──── reszta ──► web :3000
host: 127.0.0.1:8000 (api), 127.0.0.1:3000 (web) tylko dla operatora i testu dymnego
```

Obrazy `api` i `web` budują się z kodu w repozytorium. Tag obrazu to `DAAS_VERSION` z `.env`; ten sam tag służy do wycofania wersji.

## Wymagania

- VPS: 2 vCPU, 4 GB RAM, kilka GB dysku na obrazy i dane. Limity pamięci w `compose.yml`: `api` 1,5 GB, `web` 384 MB, `caddy` 128 MB (razem ok. 2 GB, reszta zostaje dla drugiej aplikacji na tym samym serwerze).
- Docker Engine 25+ z Docker Compose v2 (testowane na v5.5).
- Dla profilu `edge`: rekord DNS A/AAAA domeny na adres VPS i wolne porty 80 i 443.
- `openssl`, `curl`, `sha256sum` na hoście.

## Instalacja krok po kroku

```bash
# 1. kod w wersji, którą wdrażasz (tag albo commit)
git clone https://github.com/emsior/daas-engine.git /opt/daas-engine
cd /opt/daas-engine && git checkout v2.0.0          # po wydaniu; do prób zostaw gałąź
cd deploy

# 2. konfiguracja i sekrety (.env z prawami 0600, poza gitem)
./gen-env.sh --domain raporty.example.com
$EDITOR .env                                         # tryb poczty (smtp), limity; patrz niżej

# 3. obrazy i start (z Caddy: TLS na 80/443)
docker compose build
docker compose --profile edge up -d

# 4. pierwsza organizacja i jednorazowy klucz API (wypisany raz, nie jest zapisywany)
docker compose exec api python -m app.cli create-org --name "Nazwa firmy" --owner-email wlasciciel@example.com

# 5. test dymny przez publiczny adres
./smoke.sh --public https://raporty.example.com --wait 90 --expect-version 2.0.0
```

Właściciel organizacji loguje się linkiem z e-maila (`https://raporty.example.com/login`). Dalsze organizacje zakłada się w kokpicie (onboarding z akceptacją DPA) albo poleceniem `create-org`.

### Poczta (link logowania)

- `DAAS_EMAIL_BACKEND=console` (domyślnie po `gen-env.sh`, razem z `DAAS_ALLOW_CONSOLE_MAIL=true`): link trafia wyłącznie do pliku w `runtime/outbox/` w wolumenie (w produkcji nie ma go w dzienniku). Tylko do prób. Przy `APP_ENV=prod` API **odmawia startu** z `console`, jeśli `DAAS_ALLOW_CONSOLE_MAIL` nie jest ustawione na `true`.
- Produkcja: `DAAS_EMAIL_BACKEND=smtp` (usuń `DAAS_ALLOW_CONSOLE_MAIL` z `.env`) oraz `DAAS_EMAIL_FROM`, `DAAS_SMTP_HOST`, `DAAS_SMTP_PORT`, `DAAS_SMTP_USER`, `DAAS_SMTP_PASSWORD` (szablon w `.env`). Po zmianie `.env`: `docker compose up -d`.

### Wartości, które trzeba znać

- `DAAS_SECRET_KEY` (min. 32 znaki) podpisuje sesje i sekrety webhooków. Jego zmiana wylogowuje wszystkich i unieważnia podpisy webhooków. Kopia zapasowa danych go **nie** zawiera: zachowaj `.env` w menedżerze sekretów.
- `DAAS_API_KEY` w trybie `cloud` nie istnieje: klucze API są per organizacja i leżą w control plane jako skrót SHA-256.
- `DAAS_METRICS_TOKEN` chroni `GET /metrics` (nagłówek `Authorization: Bearer ...`). Metryki są dostępne tylko na `127.0.0.1:8000`; Caddy zwraca 404 dla `/metrics` i `/api/v1/metrics`.
- Wszystkie zmienne `DAAS_*`: tabela w [`docs/DEPLOY.md`](../docs/DEPLOY.md).

## Aktualizacja

Aktualizacja to małe, częste kroki: kopia, nowy tag, `up -d`, test dymny.

```bash
cd /opt/daas-engine/deploy
./backup.sh                                          # 1. kopia zimna (api stoi kilka sekund)
git fetch --tags && git checkout v2.0.1              # 2. nowy kod
sed -i 's/^DAAS_VERSION=.*/DAAS_VERSION=2.0.1/' .env # 3. nowy tag obrazu (nie nadpisuj starego!)
docker compose build                                 # 4. obrazy api i web z tagiem 2.0.1
docker compose --profile edge up -d                  # 5. api przy starcie wykonuje migracje control plane
./smoke.sh --public https://raporty.example.com --wait 90 --expect-version 2.0.1   # 6.
```

Zasady:

- Zawsze podbij `DAAS_VERSION` przed `build`. Gdy zbudujesz nowy kod pod starym tagiem, tracisz obraz potrzebny do wycofania.
- Nie używaj `docker image prune -a`: usuwa poprzednie tagi, do których chcesz wrócić. Bezpieczne jest `docker image prune` (tylko obrazy bez tagu).
- Migracje control plane (Alembic) idą tylko do przodu i wykonują się przy starcie `api` (`DAAS_AUTO_MIGRATE=true`). Dlatego kopia z kroku 1 jest częścią wdrożenia, nie dodatkiem.

## Rollback

Kolejność zależy od tego, czy nowa wersja zdążyła zmienić schemat bazy (sprawdź `CHANGELOG.md`: wpis o migracji).

**A. Wersja bez migracji, albo awaria widoczna zaraz po starcie**

```bash
cd /opt/daas-engine/deploy
sed -i 's/^DAAS_VERSION=.*/DAAS_VERSION=2.0.0/' .env     # poprzedni tag; obrazy api i web są lokalnie
docker compose --profile edge up -d                      # bez --build: używa istniejącego obrazu
./smoke.sh --wait 90 --expect-version 2.0.0
```

Gdy obrazu nie ma już lokalnie: `git checkout v2.0.0 && docker compose build`, potem `up -d`.

**B. Nowa wersja zmigrowała control plane**

Starszy obraz nie musi rozumieć nowszego schematu, więc przywróć kopię z kroku 1 aktualizacji razem z poprzednim tagiem:

```bash
sed -i 's/^DAAS_VERSION=.*/DAAS_VERSION=2.0.0/' .env
./restore.sh backups/daas-20261010T120000Z.tar.gz        # sprawdza SHA-256, robi kopię pre-restore, podmienia dane
./smoke.sh --wait 90 --expect-version 2.0.0
```

Dane zapisane po wykonaniu kopii przepadają (kopia pre-restore w `backups/pre-restore-*.tar.gz` pozwala je odzyskać ręcznie). Przy rollbacku Caddy nie wymaga żadnych zmian; konfigurację Caddy wycofuje się przez `git checkout` pliku `Caddyfile` i `docker compose --profile edge up -d`.

## Kopie zapasowe

```bash
./backup.sh                       # tryb zimny: api zatrzymane na czas tar, potem start
./backup.sh --online              # bez zatrzymania; kopia DuckDB może być niespójna (tylko awaryjnie)
KEEP=30 BACKUP_DIR=/srv/backups ./backup.sh
```

Archiwum `daas-<data UTC>.tar.gz` zawiera `control/` (control.db) i `runtime/` (organizacje: DuckDB, uploady, raporty, audyt; bez `outbox/`), obok niego leżą plik `.sha256` i `.meta` (wersja aplikacji). Retencja: `KEEP` najnowszych (domyślnie 14). Pliki mają prawa 0600 i zawierają dane klientów: szyfruj je i wysyłaj poza serwer (np. `restic` lub `rclone` z cron). Przykład cron (codziennie 03:15 UTC):

```
15 3 * * * cd /opt/daas-engine/deploy && ./backup.sh >> /var/log/daas-backup.log 2>&1
```

Co jakiś czas sprawdź odtworzenie na osobnym serwerze: kopia, której nie próbowano odtworzyć, nie jest kopią.

## Wspólne proxy na VPS (druga aplikacja, np. PPC Price)

Dwa zestawy compose na jednym serwerze nie mogą oba zająć portów 80 i 443. Jeśli proxy już działa, **nie używaj profilu `edge`**: `docker compose up -d` startuje wtedy tylko `api` i `web`, a porty aplikacji zostają na `127.0.0.1`.

Dodaj do istniejącego Caddyfile blok (te same reguły co w `deploy/Caddyfile`):

```
raporty.example.com {
	@metrics path /metrics /metrics/* /api/v1/metrics /api/v1/metrics/*
	handle @metrics {
		respond 404
	}
	handle /api/* {
		request_body {
			max_size 12MiB
		}
		reverse_proxy api:8000 {
			header_up X-Forwarded-For {remote_host}
			flush_interval -1
		}
	}
	# Serwer MCP (Streamable HTTP): to samo trasowanie co deploy/Caddyfile, limit ciala 4 MiB (nad DAAS_MCP_MAX_BODY_BYTES 3 MiB), bez buforowania.
	@mcp path /mcp /mcp/*
	handle @mcp {
		request_body {
			max_size 4MiB
		}
		reverse_proxy api:8000 {
			header_up X-Forwarded-For {remote_host}
			flush_interval -1
		}
	}
	# Serwer autoryzacji OAuth 2.1 konektorow MCP (rejestracja, token, cofniecie, metadane RFC 8414 / 9728): male ciala, limit 64 KiB.
	@oauth path /authorize /token /register /revoke /.well-known/oauth-*
	handle @oauth {
		request_body {
			max_size 64KiB
		}
		reverse_proxy api:8000 {
			header_up X-Forwarded-For {remote_host}
		}
	}
	handle {
		reverse_proxy web:3000 {
			header_up X-Forwarded-For {remote_host}
		}
	}
}
```

Adresy `api:8000` i `web:3000` działają, gdy kontener wspólnego proxy jest w sieci tego projektu (`daas-engine_default`): w jego compose dodaj `networks: [default, daas]` oraz `daas: { external: true, name: daas-engine_default }`. Gdy proxy działa na hoście (nie w kontenerze), użyj `127.0.0.1:8000` i `127.0.0.1:3000`. Pamiętaj o `DAAS_DOMAIN` w `.env`: z niego wynika `DAAS_COCKPIT_ORIGIN` (CORS z cookie). Proxy musi przekazywać `X-Forwarded-For` (Caddy robi to domyślnie), nie buforować odpowiedzi `text/event-stream` i nie zapisywać w dzienniku parametru `?token=` ani nagłówków `Cookie`/`Authorization` (filtr dziennika: `deploy/Caddyfile`). Ustaw też `DAAS_FORWARDED_ALLOW_IPS` na adres proxy (sekcja „Zmienne i utwardzenie kontenerów”).

## Zmienne i utwardzenie kontenerów

- **Jawna lista zmiennych.** `compose.yml` nie używa `env_file`: kontener `api` dostaje tylko zmienne trybu cloud wymienione w sekcji `environment` (z `.env` lub powłoki, gdy ustawione). `FACEIT_*`, `APIFY_*`, `ECOMMERCE_CSV_PATH`, `DISCORD_WEBHOOK_URL`, `DAAS_API_KEY` i `ANTHROPIC_API_KEY` nie przechodzą, nawet jeśli leżą w `.env`. Nową zmienną aplikacji trzeba dopisać do `compose.yml`.
- **Streszczenie modelu (LLM) jest wyłączone.** Klucz `ANTHROPIC_API_KEY` trafia do kontenera tylko przez nakładkę `compose.llm.yml`, która też ustawia `DAAS_LLM_NARRATIVE=true`. Włączenie: w `.env` dopisz `ANTHROPIC_API_KEY=...` i `COMPOSE_FILE=compose.yml:compose.llm.yml`, potem `docker compose up -d`; wyłączenie: usuń `COMPOSE_FILE`. Zasady tego, co wychodzi do dostawcy: [`docs/SECURITY.md`](../docs/SECURITY.md).
- **System plików tylko do odczytu.** `api` i `web` mają `read_only: true`; zapisywalne są wyłącznie wolumeny (`/data/control`, `/data/runtime`) i `tmpfs` (`/tmp`, dla `web` także `.next/cache`). Wszystkie kontenery mają `cap_drop: ALL` (Caddy dodatkowo `NET_BIND_SERVICE` dla portów 80/443) i `no-new-privileges`. Po aktualizacji obrazu sprawdź `./smoke.sh`; gdyby kontener zgłaszał błąd zapisu poza wolumenem, zgłoś go zamiast wyłączać `read_only`.
- **Adres klienta (`FORWARDED_ALLOW_IPS`).** Limity per IP i audyt czytają adres z `X-Forwarded-For`, ale uvicorn ufa nagłówkowi tylko od adresów z tej listy. Domyślnie `*` (`DAAS_FORWARDED_ALLOW_IPS` w `.env`): bezpieczne wyłącznie za Caddy z tego projektu (`header_up X-Forwarded-For {remote_host}` nadpisuje wartość od klienta; sprawdzone na Caddy 2.10.2, 10.10.2026), a bezpośredni dostęp do `127.0.0.1:8000` na hoście to dostęp operatora. API słucha tylko w sieci compose i na loopbacku hosta. Ze **wspólnym proxy** ustaw `DAAS_FORWARDED_ALLOW_IPS` na adres proxy widziany przez kontener (proxy w kontenerze: jego adres w sieci `daas-engine_default`; proxy na hoście: zwykle brama tej sieci, np. `172.18.0.1`, sprawdź `docker network inspect daas-engine_default`). Zbyt szeroka lista pozwala każdemu, kto dosięgnie portu API, podszyć się pod dowolny adres; zbyt wąska sprawia, że wszyscy klienci mają adres proxy i dzielą jeden limit.
- **Dziennik Caddy.** Nagłówki `X-Api-Key`, `Cookie`, `Authorization`, `X-Csrf-Token`, `Referer` i parametr `?token=` (link logowania) są usuwane lub zamazane zarówno w dzienniku dostępu, jak i w dzienniku błędów (np. 502 w trakcie restartu API).

## Operacje

- Stan: `docker compose ps`, `docker compose logs -f api` (dziennik JSON z `request_id`), `docker compose logs caddy` (dostęp; nagłówki z kluczami i cookie oraz token linku logowania są usuwane z dziennika).
- Metryki: `curl -H "Authorization: Bearer $DAAS_METRICS_TOKEN" http://127.0.0.1:8000/metrics`.
- Restart po zmianie `.env`: `docker compose up -d` (kontenery z nową konfiguracją są tworzone od nowa, dane zostają w wolumenach).
- Zapora: otwórz tylko 22, 80 i 443. Porty aplikacji są na loopbacku; nie zmieniaj ich na `0.0.0.0`.
- Limit `api`: jeden proces roboczy (DuckDB ma jednego pisarza, liczniki limitów są w pamięci). Nie ustawiaj `WEB_CONCURRENCY` ani `UVICORN_WORKERS` powyżej 1: start zostanie odmówiony.

## Znane ograniczenia i uwagi

- Jeden węzeł, brak HA: control plane to SQLite w wolumenie (`DAAS_CONTROL_DB_URL` pozwala przejść na Postgres, wymaga dodatkowej zależności `asyncpg`: `requirements-postgres.lock`, dodatek `postgres` w `pyproject.toml`; obraz `api` jej nie zawiera; CI sprawdza control plane na PostgreSQL 16).
- Aktualizacja zatrzymuje `api` na czas restartu (sekundy); trwające zadania w locie kończą się jako `failed` z kodem `abandoned`.
- `Caddyfile` przeszedł `caddy validate` (2.10) i próbę filtra dziennika na lokalnym Caddy, a `compose.yml` `docker compose config`; obrazy nie były budowane ani uruchamiane lokalnie (brak demona Dockera; budowę obrazów sprawdza job `docker` w CI), więc pełny rozruch na docelowym VPS to zadanie operatora: wykonaj `./smoke.sh --public ...` i potraktuj go jako bramę przyjęcia.
- Obrazy bazowe są przypięte tagami (`python:3.12-slim`, `node:22-alpine`, `caddy:2`), nie skrótami. Dependabot (ekosystem `docker`) podpowiada podbicia.

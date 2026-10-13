# DaaS Engine — automatyczny raport tygodniowy z Twoich danych
Live: https://prochpc.pl

*[English README →](README.md)*

**Wysyłasz eksport CSV/XLSX ze sklepu, CRM-a albo księgowości → dostajesz gotowy raport HTML.
Co tydzień, automatycznie, bez ręcznego składania w Excelu.**

▶ **[Demo wideo — 50 s](docs/daas_demo.mp4)** (bez dźwięku, wystarczy obejrzeć)

Co liczy: przychód, koszt, marża, średnia wartość zamówienia, zwroty, top produkty i kategorie,
porównanie tydzień do tygodnia, zamówienia stratne i anomalie — plus sekcja **PRAKTYCZNE WNIOSKI**
z konkretnymi akcjami. Liczby pochodzą wyłącznie z Twojego pliku; nic nie jest generowane „z głowy".

Radzi sobie z realnymi polskimi eksportami: separator `;`, kwoty `1 299,00 zł`, rabaty `30%`,
statusy `Zrealizowane / Zwrot / Anulowane`, kodowanie `cp1250`, nagłówki po polsku i angielsku. Przyjmuje CSV i XLSX (bez makr).

![Raport HTML wygenerowany z pliku klienta (client_export_pl.csv): KPI, marża, zamówienia stratne, anomalie, PRAKTYCZNE WNIOSKI](docs/demo_report.png)

*Powyżej: raport z przykładowego eksportu sklepu (15 zamówień) — od uploadu pliku do gotowego HTML w < 1 s, bez ręcznej roboty.*

Kontakt: prochpc@gmail.com

*In short (EN): send any messy sales export (CSV/XLSX) → get an automated weekly HTML report with
KPIs, week-over-week deltas, loss-making orders and anomaly flags. FastAPI + DuckDB + Next.js cockpit, tests included.*

---

## Co dostajesz

- **Raport tygodniowy z Twojego pliku** — HTML (do wydruku do PDF), Markdown i JSON.
- **Kokpit** (tryb `cloud`) — logujesz się linkiem z e-maila (bez haseł), wgrywasz plik, widzisz postęp na żywo, otwierasz raport i masz archiwum. Blok **Sprawdź sam** liczy skrót SHA-256 pobranego raportu w przeglądarce; plik nie jest wysyłany na serwer.
- **API** — klucz per organizacja z zakresami, postęp na żywo (SSE), podpisane webhooki. Przykłady: [`docs/API.md`](docs/API.md).
- **Kolektor artefaktów + raport statusu** — kolektor PowerShell tylko do odczytu (`tools/uksc-collector.ps1`) zbiera ustawienia techniczne stacji Windows do paczki JSON ze skrótami; raport pokazuje kontrolę, wymóg (UKSC/NIS2), status i skrót artefaktu.
- **Twoje dane zostają Twoje** — każda organizacja ma własny katalog danych i własną bazę DuckDB; pliki wygasają po ustawionej retencji; nic nie idzie do dostawcy AI bez Twojej zgody.

Dostawa: uruchamiane przyciskiem w kokpicie, webhookiem n8n albo automatycznie wg harmonogramu, z alertem przy
spadku przychodu ≥15% lub marży ≥3 pp. Dostajesz kod źródłowy i workflow — rozwiązanie jest Twoje.
Możesz hostować u siebie (Docker lub `pip`) albo zlecić hosting.

### Dwa tryby, jeden kod (`DAAS_MODE`)

| | `selfhost` | `cloud` |
|---|---|---|
| Dla kogo | wszystko na Twoim serwerze lub komputerze | hosting PPC albo własna usługa dla wielu organizacji |
| Organizacje | jedna (`default`) | wiele, z członkami i rolami |
| Logowanie | jeden klucz instancji (`X-API-Key`) | link z e-maila dla ludzi, klucze API dla maszyn |
| Uruchomienie raportu | `POST /run` (synchronicznie) albo zadania API v1 | zadania API v1 (asynchronicznie, postęp SSE) |
| Kokpit | opcjonalny | w zestawie (`web/`) |
| Start | `docker compose up` w katalogu głównym albo `pip` | `deploy/compose.yml` na VPS |

---

## Uruchomienie — Docker Compose, tryb cloud (jeden VPS)

```bash
cd deploy
./gen-env.sh --domain raporty.example.com     # tworzy .env z losowymi sekretami (nie nadpisuje istniejącego)
docker compose build
docker compose --profile edge up -d           # api + web + Caddy (TLS)
docker compose exec api python -m app.cli create-org --name "Moja Firma" --owner-email wlasciciel@example.com
./smoke.sh --public https://raporty.example.com --wait 90
```

Logowanie: `https://raporty.example.com/login` i link z e-maila (w `.env` ustaw `DAAS_EMAIL_BACKEND=smtp`; przy domyślnym `console` link jest w `docker compose logs api`).
Instalacja, aktualizacja, rollback, kopie zapasowe i dzielenie VPS z drugą aplikacją: [`deploy/README.md`](deploy/README.md).

Próba lokalna bez domeny i TLS: dopisz do `deploy/.env` trzy linie, uruchom `docker compose up -d --build` i otwórz http://127.0.0.1:3000:

```
NEXT_PUBLIC_API_ORIGIN=http://127.0.0.1:8000
DAAS_COCKPIT_ORIGIN=http://127.0.0.1:3000
DAAS_COOKIE_SECURE=false
```

## Uruchomienie — Docker Compose, tryb selfhost (z n8n)

```bash
cp .env.example .env          # ustaw DAAS_API_KEY (min. 16 znaków; openssl rand -hex 24) przed pierwszym startem
docker compose up --build
```

- API: http://127.0.0.1:8000 (Swagger pod `/docs` tylko z `APP_ENV=dev`)
- n8n: http://127.0.0.1:5678 (login z `.env`: `N8N_BASIC_AUTH_USER` / `N8N_BASIC_AUTH_PASSWORD`)

Import workflow do n8n: **Workflows → Import from file** (pliki są też zamontowane w kontenerze pod `/srv/workflows/`):

- `n8n/daas_workflow.json` — Manual + harmonogram (pon 07:00) → `POST /run` dla obu pipeline'ów → formatowanie → IF `needs_attention` (run nieudany, brak danych, **spadek przychodu ≥15 % tydzień-do-tygodnia lub marży ≥3 pp**) → Alert / Digest.
![Workflow n8n: webhook → POST /upload → walidacja → POST /run → odpowiedź z linkiem do raportu](docs/n8n_client_workflow.png)

- `n8n/daas_client_report.json` — webhook `POST /webhook/daas-report` (multipart `file` + `client_name`) → `POST /upload` → `POST /run` → odpowiedź z linkiem do raportu HTML. To jest "przycisk" dla klienta: wysyła plik, dostaje link. Ustaw `DAAS_PUBLIC_URL` w env n8n, jeśli app jest za reverse proxy.

Baza i raporty trafiają na hosta do `./runtime/` (wolumen).

## Uruchomienie — Python bez Dockera

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements-dev.lock && pip install --no-deps -e .
export DAAS_API_KEY="$(openssl rand -hex 24)"
python -m app.main                                     # http://127.0.0.1:8000
```

**Windows, jedno kliknięcie:** `URUCHOM_DAAS_PYTHON.ps1` (prawy klik → *Uruchom w programie PowerShell*). Skrypt sprawdza Pythona 3.11+, tworzy `.venv`, instaluje zależności, odpala testy, startuje API na `:8000`, wgrywa `data/fixtures/client_export_pl.csv` przez `/upload` → `/run` i otwiera raport HTML w przeglądarce. Brak Pythona: `winget install Python.Python.3.12`.

Ręcznie (PowerShell):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env      # ustaw DAAS_API_KEY
pytest
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Jeśli `Activate.ps1` jest blokowany: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. n8n bez Dockera: `npx n8n` (Node 18+); w workflow podmień URL `http://app:8000/run` na `http://localhost:8000/run`.

Tryb cloud ze źródeł: `DAAS_MODE=cloud DAAS_SECRET_KEY=$(openssl rand -hex 32) python -m app.main`, kokpit: `cd web && npm ci && npm run build` (szczegóły: [`web/README.md`](web/README.md)).

---

## Flow „plik klienta → raport"

Tryb cloud, API v1 (pełny opis: [`docs/API.md`](docs/API.md)):

```bash
export DAAS=http://127.0.0.1:8000 KEY="daas_..." ORG="01J..."      # klucz i id organizacji z kokpitu lub z create-org

# 1. upload — zwraca wykryte mapowanie kolumn
curl -s -H "X-API-Key: $KEY" -F "file=@data/fixtures/client_export_pl.csv" "$DAAS/api/v1/orgs/$ORG/uploads" | jq

# 2. zadanie z identyfikatorem uploadu — 202 Accepted (+ nagłówek Location)
curl -s -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"pipeline":"ecommerce_demo","upload_id":"<id z kroku 1>","client_name":"Sklep Demo"}' "$DAAS/api/v1/orgs/$ORG/jobs" | jq

# 3. po statusie succeeded: raport (HTML w przeglądarce / MD / JSON)
curl -s -H "X-API-Key: $KEY" "$DAAS/api/v1/orgs/$ORG/runs/<run_id>/report?fmt=html" -o raport.html
```

Tryb selfhost, trasy legacy (każda poza `/health` i `/ready` wymaga `X-API-Key` z `DAAS_API_KEY`):

```bash
curl -s -H "X-API-Key: $DAAS_API_KEY" -F "file=@data/fixtures/client_export_pl.csv" -F "client_name=Sklep Demo" localhost:8000/upload | jq
# {"rows":15,"confidence":1.0,"ready":true,"mapping":{"revenue":"Wartość brutto",...},
#  "run_payload":{"pipeline":"ecommerce_demo","upload_id":"<32 znaki hex>","client_name":"Sklep Demo"}}
curl -s -X POST localhost:8000/run -H "X-API-Key: $DAAS_API_KEY" -H "Content-Type: application/json" \
  -d '{"pipeline":"ecommerce_demo","upload_id":"<upload_id>","client_name":"Sklep Demo"}' | jq
curl -s -H "X-API-Key: $DAAS_API_KEY" "localhost:8000/reports/ecommerce_demo/latest?fmt=html" -o raport.html
```

`run_status`: `SUCCESS` / `PARTIAL` (0 rekordów) / `FAILED` (wyjątek — nadal HTTP 200, szczegół w `error`).
`data_status`: `LIVE_DATA` / `MOCK_DATA` / `BLOCKED_MISSING_SECRET` (brak sekretu **i** brak fixture — nie powinno się zdarzyć w repo).

Mapper (`app/sources/csv_mapper.py`): aliasy PL/EN dla `order_id, date, product, category, quantity, revenue, cost, profit, discount, status`; auto-detekcja separatora (`;`/`,`/tab) i kodowania (utf-8/cp1250); liczby `1 299,00 zł` / `1,299.00` / `12,5%`; statusy `Zrealizowane/Wysłane/Zwrot/Anulowane/Oczekujące` → `completed/refunded/cancelled/pending`. Brakujące kolumny są dopełniane (`profit = revenue - cost`, `status = completed`, `category = Inne`) i raportowane w polu `generated`; `confidence` mówi, ile z kolumn kluczowych znaleziono w pliku. XLSX przechodzi tę samą ścieżkę po konwersji do CSV (limity: `docs/pilot/INPUT_REQUIREMENTS_PL.md`).

---

## Architektura

Jeden proces FastAPI, control plane (konta, klucze, zadania, audyt; SQLite + Alembic) i data plane (katalog `runtime/tenants/<org_id>/` z własnym plikiem DuckDB, uploadami i raportami). Diagram, tryby, wykonawca zadań: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

```
app/
  api/         routes.py (legacy, selfhost), v1/ (organizacje, klucze, uploady, zadania, runy, webhooki)
  control/     control plane: modele, migracje, tenancy, executor zadań, webhooki
  core/        config.py (Pydantic Settings), auth, limity, upload_guard, import XLSX, redakcja, observability
  storage/     duckdb_client.py (schemat, upsert, odczyt runów)
  sources/     cs2_source.py, ecommerce_source.py, csv_mapper.py, uksc_source.py (LIVE → fixture fallback)
  transforms/  cs2_transform.py, ecommerce_transform.py, uksc_transform.py (pandas)
  reporting/   generator.py (HTML + Markdown + JSON), narracja
  pipelines/   runner.py (orkiestracja, kontrakt "nigdy nie rzuca")
web/           kokpit Next.js 16
deploy/        compose, Caddyfile, gen-env.sh, backup.sh, restore.sh, smoke.sh
data/fixtures/ dane przykładowe (syntetyczne)
n8n/           workflowy
runtime/       dane i raporty (wolumen, gitignore)
tests/         testy offline
```

Trzy pipeline'y na jednym silniku (ta sama baza, ten sam kontrakt API):

| pipeline | domena | metryki | źródło LIVE | fallback |
|---|---|---|---|---|
| `cs2_demo` | esports statistics (portfolio) | win rate, K/D, rating, ADR, HS%, form score, breakdown map | FACEIT Data API (`FACEIT_API_KEY` + `FACEIT_PLAYER_NICKNAME`) | `data/fixtures/cs2_matches.json` |
| `ecommerce_demo` | e-commerce B2B | zamówienia, przychód, koszt, zysk, marża %, AOV, zamówienia stratne, anomalie, top kategorie/produkty | Apify dataset (`APIFY_TOKEN` + `APIFY_DATASET_ID`), własny CSV/XLSX (`ECOMMERCE_CSV_PATH`) albo upload klienta | `data/fixtures/ecommerce_orders.csv` |
| `uksc_evidence` | kolektor artefaktów + raport statusu (UKSC/NIS2) | kontrole techniczne stacji Windows zmapowane na wymogi UKSC, status i skrót artefaktu każdej kontroli, `package_sha256` | paczka JSON z `tools/uksc-collector.ps1` | `data/fixtures/uksc/host_compliant.json` |

**Zasada nr 1: brak klucza nie jest błędem.** Pipeline automatycznie przełącza się na fixtures i oznacza wynik jako `MOCK_DATA`. Wszystko działa od pierwszego uruchomienia bez żadnego sekretu źródłowego.

**Zasada nr 2: plik klienta zamiast integracji.** Upload przyjmuje dowolny eksport CSV lub XLSX (Allegro, Shoper, WooCommerce, Baselinker, Excel „po swojemu"), mapuje kolumny heurystycznie i zwraca gotowy payload. Wynik = `LIVE_DATA`, raport HTML pod linkiem. Demo dla klienta: 2 requesty, zero kodu.

Przepływ jednego runu: `source.fetch()` → zapis surowych rekordów do DuckDB (klucz `(run_id, id)`, idempotentnie) → `transform()` → `ReportGenerator.write()` → metadane runu do tabeli `runs` → wynik `RunResult`.

n8n **nie wykonuje** Pythona (żadnego `Execute Command`) — wyłącznie `HTTP Request`. Dzięki temu app można podmienić na cokolwiek bez ruszania workflow.

---

## Sekrety i fallback

Pełna lista zmiennych z wartościami domyślnymi: [`docs/DEPLOY.md`](docs/DEPLOY.md) i [`.env.example`](.env.example).

| zmienna | używana przez | brak → |
|---|---|---|
| `DAAS_API_KEY` | tryb `selfhost`: nagłówek `X-API-Key` | trasy chronione odpowiadają `401`, start poza loopbackiem odmówiony |
| `DAAS_SECRET_KEY` | tryb `cloud`: podpis sesji i webhooków (min. 32 znaki) | start odmówiony |
| `FACEIT_API_KEY` (+ `FACEIT_PLAYER_NICKNAME`) | `cs2_demo` | fixture `cs2_matches.json`, `MOCK_DATA` |
| `APIFY_TOKEN` (+ `APIFY_DATASET_ID`) | `ecommerce_demo` | `ECOMMERCE_CSV_PATH` jeśli ustawiony, inaczej fixture `ecommerce_orders.csv`, `MOCK_DATA` |
| `LIQUIPEDIA_USER_AGENT` | zarezerwowane | ignorowane |
| `DISCORD_WEBHOOK_URL` | `notify=true` w `/run` | powiadomienie pominięte, run OK |
| `ANTHROPIC_API_KEY` + zgoda (`DAAS_LLM_NARRATIVE` / ustawienie organizacji) | streszczenie wykonawcze w raporcie | deterministyczny szablon (`narrative_source: TEMPLATE`) |

Błąd sieci przy LIVE (timeout, 401, zmiana schematu API) również kończy się fallbackiem na fixture — pipeline nie wywraca się z powodu źródła. Powód jest zawsze w `source_detail`.

---

## Metryki

### CS2 (`cs2_demo`)
- **win_rate** — % meczów wygranych (remis nie liczy się jako wygrana).
- **kd_ratio** — suma killi / suma śmierci.
- **avg_rating** — średni rating meczowy (HLTV 2.0-like; dla FACEIT liczony aproksymacją z KPR/DPR/ADR).
- **avg_adr** — średni damage per round.
- **hs_pct** — % killi headshotem.
- **form_score (0-100)** — ważona forma z ostatnich 5 meczów, ostatni mecz waży najwięcej (waga 0.6ⁿ). Składowe: 35% rating (cap 1.6), 25% K/D (cap 2.0), 20% ADR (cap 120), 20% wynik. Kalibracja: rating 1.0 / K/D 1.0 / ADR 75 / WR 50% ≈ 50 pkt.
- **form_trend** — `UP`/`FLAT`/`DOWN`: różnica średniego ratingu drugiej połowy okna vs pierwszej (próg ±0.05).
- **map_breakdown** — mecze, WR, rating, ADR, K/D per mapa; `best_map`/`worst_map` po WR, potem ratingu.

### E-Commerce (`ecommerce_demo`)
- Do P&L liczą się statusy `completed` i `pending`; `refunded`/`cancelled` są raportowane, ale wykluczone z przychodu.
- **revenue_total / cost_total / profit_total**, **margin_pct** = profit / revenue.
- **avg_order_value**, **avg_discount_pct**.
- **loss_orders** — zamówienia z `profit < 0` (posortowane od najgorszego).
- **anomalies** — typy: `NEGATIVE_PROFIT` (zysk < 0), `DEEP_DISCOUNT` (rabat ≥ 25%), `MARGIN_OUTLIER` (marża < mediana − 2·MAD w kategorii), `REVENUE_OUTLIER` (przychód > Q3 + 1.5·IQR).
- **top_categories** — przychód, zysk, marża, udział w przychodzie; **top_products** — top 5 po przychodzie.
- **weekly** (v0.3) — agregacja per tydzień ISO (pon–nd): zamówienia, przychód, zysk, marża, AOV, liczba dni z danymi.
- **wow** (v0.3) — ostatni tydzień vs poprzedni: delta przychodu %, zysku %, marży (pp), zamówień; flaga `partial_week`, gdy ostatni tydzień jest niepełny. `null`, gdy w pliku jest mniej niż 2 tygodnie. W odpowiedzi `POST /run` pole `metrics.wow` + `metrics.weeks_in_data` (do IF-a w n8n: alert przy spadku przychodu). W raporcie: linia w WNIOSKU, sekcja „Tydzień do tygodnia”, akcje w PRAKTYCZNYCH WNIOSKACH (spadek przychodu ≥15 %, ruch marży ≥3 pp).

Każdy raport Markdown kończy się sekcją **PRAKTYCZNE WNIOSKI** — konkretne akcje wynikające z liczb.

### Streszczenie (narrative)
Raport zaczyna się od 3-5 zdań streszczenia. Domyślnie pisze je deterministyczny szablon z policzonych metryk (`narrative_source: TEMPLATE`). Streszczenie przez model (`NARRATIVE_MODEL`, domyślnie `claude-sonnet-4-5`) jest **opt-in**: wymaga zgody (`DAAS_LLM_NARRATIVE=true` w trybie `selfhost`, ustawienie organizacji `llm_narrative` w trybie `cloud`) oraz `ANTHROPIC_API_KEY`; sam klucz niczego nie włącza. Zasada: **LLM nie liczy** — dostaje policzone metryki oraz nazwy kilku największych kategorii i map z pliku (bez identyfikatorów zamówień, nazw produktów i nazwy klienta) i ma je tylko opisać; treść nie jest weryfikowana z danymi, więc czytaj ją jak szkic. Raport oznacza takie streszczenie jako wygenerowane przez model (`narrative_source: LLM`).

### Raport HTML
Samodzielny plik (inline CSS, bez zależności zewnętrznych), otwiera się z dysku, z maila, drukuje do PDF (Ctrl+P). Dla każdego runu: `runtime/reports/<pipeline>_<ts>_<id>.html` + `<pipeline>_latest.html`.

---

## Dodanie nowego pipeline'u (source-agnostic w praktyce)

1. `app/sources/<x>_source.py` — klasa z `fetch(force_mock) -> SourceResult` (LIVE → fixture).
2. `app/transforms/<x>_transform.py` — `transform_<x>(records) -> Metrics` (Pydantic).
3. Tabela w `SCHEMA_SQL` + metoda `write_<x>` w `duckdb_client.py`.
4. Wpis w `PIPELINES` i gałąź w `PipelineRunner.run`.
5. Renderer Markdown w `reporting/generator.py`.

Kontrakt API i workflow n8n nie zmieniają się.

---

## Testy

```bash
pytest                                  # backend, offline
cd web && npm test && npm run e2e       # kokpit: testy jednostkowe; e2e wymaga API w trybie cloud (web/README.md)
```

CI (`.github/workflows/ci.yml`): pytest na Python 3.11/3.12/3.13, testy control plane na prawdziwym PostgreSQL (blokady wierszy, migracje), smoke API przez curl, skanery (gitleaks, semgrep, zizmor, pip-audit), kokpit (eslint, tsc, vitest, build), testy end-to-end Playwright w Chromium, Firefoksie i WebKicie, Lighthouse (mobile), build obrazów Docker (API i kokpit) wraz ze smoke w kontenerze.

Szczegółowa lista kontrolna: [VALIDATION.md](VALIDATION.md). Historia zmian: [CHANGELOG.md](CHANGELOG.md).

## Dokumentacja

[`docs/API.md`](docs/API.md) · [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/DEPLOY.md`](docs/DEPLOY.md) · [`deploy/README.md`](deploy/README.md) · [`docs/SECURITY.md`](docs/SECURITY.md) · [`docs/DPA_v1_PL.md`](docs/DPA_v1_PL.md) · materiały pilota: [`docs/pilot/`](docs/pilot)

---

Kontakt: prochpc@gmail.com · https://prochpc.pl

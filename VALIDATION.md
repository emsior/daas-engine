# VALIDATION — lista kontrolna

Legenda: ✅ zweryfikowane w tym repo (uruchomione) · ⚠ zaimplementowane, wymaga środowiska użytkownika (Docker / klucz API) · ❌ poza zakresem

## IMPLEMENTED

| # | wymaganie | status | gdzie |
|---|---|---|---|
| 1 | n8n i app jako osobne usługi w sieci `daas_network` | ⚠ (compose gotowy, nieuruchomiony tu bez Dockera) | `docker-compose.yml` |
| 2 | n8n orkiestruje wyłącznie przez `HTTP Request POST http://app:8000/run` — zero `Execute Command` | ✅ (JSON zwalidowany) | `n8n/daas_workflow.json` |
| 3 | Payload `{"pipeline": "cs2_demo" \| "ecommerce_demo"}` | ✅ | `app/core/models.py::RunRequest` |
| 4 | Odpowiedź: `run_id, pipeline, run_status, data_status, records_processed, report_path, execution_time_sec` | ✅ | `RunResult`, test `test_api_run_and_latest` |
| 5 | Odczyt `FACEIT_API_KEY, LIQUIPEDIA_USER_AGENT, APIFY_TOKEN, DISCORD_WEBHOOK_URL` z `.env` | ✅ | `app/core/config.py` |
| 6 | Brak klucza → fixtures + `MOCK_DATA`, bez wyjątku | ✅ | `cs2_source.py`, `ecommerce_source.py`, testy |
| 7 | Błąd LIVE (sieć/401) → fallback, nie crash | ⚠ (kod: try/except → fixture; nie testowane z realnym kluczem) | `_fetch_live` |
| 8 | CS2: win rate, K/D, rating, ADR, form score | ✅ | `cs2_transform.py` |
| 9 | E-com: zamówienia, przychód, zysk, marża, anomalie stratne, top kategorie | ✅ | `ecommerce_transform.py` |
| 10 | Wspólna DuckDB `runtime/daas.duckdb`, tabele `runs, cs2_matches, ecommerce_orders` | ✅ | `duckdb_client.py` |
| 11 | Idempotentny zapis (INSERT OR REPLACE po kluczu) | ✅ | test `test_duckdb_persistence` |
| 12 | Raporty JSON + Markdown w `runtime/reports/` + `*_latest.*` | ✅ | `reporting/generator.py` |
| 13 | Endpointy `/health /pipelines /run /runs/latest` (+ `/runs`, `/runs/{id}`, `/stats`) | ✅ | `api/routes.py` |
| 14 | CORS + routery | ✅ | `app/main.py` |
| 15 | Dockerfile python:3.11-slim, uvicorn :8000, healthcheck | ⚠ (nie zbudowany tu) | `Dockerfile` |
| 16 | Testy pytest: oba pipeline'y mock, tymczasowa DuckDB, kody HTTP | ✅ 11 passed | `tests/test_pipelines.py` |
| 17 | README po polsku (architektura, Docker, PowerShell, curl, metryki) | ✅ | `README.md` |
| **v0.2** | | | |
| 18 | `POST /upload` CSV/XLSX klienta → `runtime/uploads/`, mapowanie kolumn, `run_payload` | ✅ | `api/routes.py`, `sources/csv_mapper.py` |
| 19 | Mapper: polskie nagłówki, `;` separator, `1 299,00 zł`, `30%`, statusy PL, cp1250 | ✅ (fixture `client_export_pl.csv`, confidence 1.0) | `csv_mapper.py`, testy |
| 20 | `RunRequest.source_path` + `client_name` → `LIVE_DATA` z pliku klienta | ✅ | `runner.py`, `ecommerce_source.py` |
| 21 | Raport HTML (self-contained) + `GET /reports/{pipeline}/latest`, `GET /reports/run/{id}` | ✅ (screenshot Playwright) | `reporting/html.py` |
| 22 | Streszczenie wykonawcze: LLM (ANTHROPIC_API_KEY) z fallbackiem na szablon; liczby tylko z metryk | ✅ fallback; ⚠ ścieżka LLM bez klucza nietestowana live | `reporting/narrative.py` |
| 23 | Brak `source_path` → `run_status: FAILED` z `error`, HTTP 200 | ✅ | `runner.py` |
| 24 | n8n webhook flow: plik → upload → run → link HTML | ✅ JSON zwalidowany; ⚠ nieodpalony w n8n | `n8n/daas_client_report.json` |
| 25 | CI GitHub Actions: pytest 3.11/3.12 + smoke API + docker build | ⚠ YAML zwalidowany, uruchomi się po pushu | `.github/workflows/ci.yml` |
| 26 | Migracja schematu DuckDB v0.1 → v0.2 (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) | ✅ | `duckdb_client.py` |

## LOCAL COMMANDS

```bash
# 0. zależności (lub: pip install -e ".[dev]")
pip install fastapi "uvicorn[standard]" duckdb pydantic pydantic-settings pandas httpx pyyaml pytest

# 1. testy
pytest -v

# 2. API
uvicorn app.main:app --port 8000

# 3. smoke (drugi terminal)
curl -s localhost:8000/health
curl -s localhost:8000/pipelines
curl -s -X POST localhost:8000/run -H "Content-Type: application/json" -d '{"pipeline":"cs2_demo"}'
curl -s -X POST localhost:8000/run -H "Content-Type: application/json" -d '{"pipeline":"ecommerce_demo"}'
curl -s localhost:8000/runs/latest
curl -s localhost:8000/stats

# 4. Docker
cp .env.example .env
docker compose up --build
# n8n: http://localhost:5678 → Import from file → n8n/daas_workflow.json → Execute workflow

# 4b. Flow klienta
curl -s -F "file=@data/fixtures/client_export_pl.csv" -F "client_name=Sklep Demo" localhost:8000/upload
curl -s "localhost:8000/reports/ecommerce_demo/latest" -o raport.html && xdg-open raport.html

# 5. Inspekcja bazy
python -c "import duckdb; c=duckdb.connect('runtime/daas.duckdb'); print(c.sql('SELECT run_id, pipeline, run_status, data_status, records_processed FROM runs ORDER BY started_at DESC'))"
```

## EXPECTED RESULTS

| krok | oczekiwane |
|---|---|
| `pytest -v` | `32 passed` |
| `GET /health` | 200, `"status":"ok"`, `secrets` = mapa 4 kluczy → `false` (bez .env) |
| `GET /pipelines` | 200, 2 pozycje, `expected_data_status: "MOCK_DATA"` |
| `POST /run cs2_demo` | 200, `run_status: SUCCESS`, `data_status: MOCK_DATA`, `records_processed: 10`, `metrics.form_score` w 0-100, `metrics.win_rate: 60.0` |
| `POST /run ecommerce_demo` | 200, `SUCCESS`, `MOCK_DATA`, `records_processed: 20`, `metrics.margin_pct ≈ 25.08`, `loss_orders_count: 3` |
| `POST /run {"pipeline":"x"}` | 422 |
| `GET /runs/latest` przed jakimkolwiek runem | 404 `"no runs yet"` |
| `GET /runs/latest` po runach | 200, ostatni run |
| `GET /stats` po 2 runach | `{"runs":2,"cs2_matches":10,"ecommerce_orders":20}` |
| `POST /upload` z `data/fixtures/client_export_pl.csv` | 200, `rows: 15`, `confidence: 1.0`, `ready: true`, `mapping.revenue: "Wartość brutto"` |
| `POST /run` z `run_payload` z uploadu | 200, `LIVE_DATA`, `records_processed: 15`, `margin_pct ≈ 30.26`, `loss_orders_count: 2`, `narrative_source: TEMPLATE` |
| `GET /reports/ecommerce_demo/latest` | 200 `text/html`, box "Streszczenie", tabele, PRAKTYCZNE WNIOSKI |
| `POST /upload` z `.exe` / pustym plikiem / CSV bez przychodu | 415 / 400 / 422 |
| `runtime/reports/` | `cs2_demo_<ts>_<id>.md/.json`, `ecommerce_demo_<ts>_<id>.md/.json`, `*_latest.md/.json` |
| raport MD | sekcje: nagłówek ze statusem źródła, WNIOSEK, metryki, tabele, PRAKTYCZNE WNIOSKI |
| n8n Execute | 2 itemy po `Format report`; oba z `is_mock: true`, `needs_attention: false` → gałąź `Digest OK` |
| z ustawionym `FACEIT_API_KEY` + `FACEIT_PLAYER_NICKNAME` | `data_status: LIVE_DATA`, `source_detail: "FACEIT live: player=..."`; przy złym kluczu → fallback `MOCK_DATA` z powodem w `source_detail` |

## ZNANE OGRANICZENIA

- DuckDB = jeden writer na plik; runner ma `RLock`, ale dwa procesy uvicorn (`--workers 2`) na jednej bazie się pobiją. Trzymaj 1 worker albo przejdź na Postgres.
- Rating dla danych FACEIT LIVE to aproksymacja (FACEIT nie zwraca HLTV 2.0).
- `LIQUIPEDIA_USER_AGENT` jest tylko wykrywany — brak jeszcze adaptera Liquipedia (miejsce na kontekst drużynowy / turniejowy).
- Powiadomienie Discord jest fire-and-forget (5 s timeout), błąd loguje warning i nie wpływa na `run_status`.

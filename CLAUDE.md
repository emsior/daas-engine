@AGENTS.md

# CLAUDE.md — DaaS Engine (PPC)

> Kontekst, którego nie da się wywnioskować z kodu. Zasady dla agentów są w `AGENTS.md`
> (importowane w pierwszej linii). Historia zmian: `CHANGELOG.md`. Architektura: `docs/ARCHITECTURE.md`.

## Czym jest

**DaaS Engine** — silnik raportowy dla małych firm bez API. Klient wysyła eksport ze sprzedaży
→ pipeline liczy metryki → klient dostaje gotowy raport HTML. To produkt komercyjny marki PPC;
oferta i cennik są wyłącznie na https://prochpc.pl (landing „Raport w Poniedziałek").
Każda zmiana ma odpowiadać na pytanie „czy to przybliża do sprzedaży", nie „czy to ładniejszy kod".

## Status: v1.0.0 — Maintenance (od 28.09.2026)

- Oferta żywa, kod zamrożony (warunki odblokowania: `AGENTS.md`, zasada 8).
- Roadmapa techniczna v2 (Polars, pandera, DLQ, Evidence) jest zaparkowana do 3 płacących klientów.
- Przegląd trybu utrzymania: 06.12.2026 — zostaje albo archiwum.

## Stack

| Warstwa | Technologia |
|---|---|
| Język | Python ≥ 3.11 — CI: 3.11 i 3.12, obraz Docker `3.12-slim`, lokalny dev 3.12.10 |
| API | FastAPI |
| Baza / silnik zapytań | DuckDB |
| Przetwarzanie | pandas |
| Orkiestracja | n8n (webhook + harmonogram; definicje w `n8n/`) |
| Testy | pytest — **195** na `main` (pełny przebieg ~100 s) |
| CI | GitHub Actions: `test` (3.11, 3.12), `security` (gitleaks, semgrep, zizmor), `docker`; Dependabot; akcje przypięte do SHA |

⚠ **Tu NIE MA JavaScriptu.** Nie ma `package.json`, `node_modules` ani TypeScriptu.
Jeśli prompt każe czytać `package.json` — przeczytaj `pyproject.toml` / `requirements.txt`.

## Przepływ i pipeline'y

```
POST /upload                              → przyjmuje CSV klienta (mapper nazw kolumn PL/EN)
POST /run                                 → uruchamia pipeline, liczy metryki
GET  /reports/{pipeline}/latest?fmt=html  → oddaje gotowy raport
```

- `ecommerce_demo` — przychód, koszt, marża, tydzień-do-tygodnia (`weekly` + `wow`) z akcjami
  wyprowadzonymi z delt; alert n8n przy spadku przychodu ≥ 15% albo marży ≥ 3 p.p.
- `cs2_demo` — esports statistics (FACEIT API albo fixture).
- `uksc_evidence` — dowody zgodności UKSC/NIS2: collector PowerShell (read-only) → paczka JSON
  z hashami → raport „kontrola → artykuł → status → dowód" z `package_sha256` do weryfikacji.

## Stan repo przy v1.0.0

- Ostatni commit kodu: `d489db3` (PR #11). Tag `v1.0.0` wskazuje merge PR-a zamykającego (tylko dokumentacja).
- PR #1–#11: 10 zmergowanych, #7 zamknięty bez merge.
- CI zielone. Ochrona `main`: wymagany PR i checki `test (3.11)`, `test (3.12)`; force push zablokowany.
- Warstwa policy (`app/core/policy.py`) jest podpięta w runtime `/upload`; flaga `DAAS_POLICY_ENFORCE`
  domyślnie `false`.
- `/upload` od PR #3 przyjmuje wyłącznie `.csv` (mniejsza powierzchnia ataku); XLSX `ecommerce_demo` czyta
  tylko z lokalnej ścieżki. Landing obiecuje „CSV lub XLSX", więc XLSX od klienta
  trzeba przed `/upload` zapisać jako CSV albo przetworzyć lokalnie.

## Pułapki środowiska (sprawdzone na maszynie deweloperskiej)

| Problem | Obejście |
|---|---|
| Docker Desktop na Windows nie startuje (WSL / Hypervisor Platform) | uruchamiaj natywnie `URUCHOM_DAAS_PYTHON.ps1`; Docker dopiero pod self-host klienta |
| `.git/HEAD.lock`, `index.lock` zostają po gicie uruchamianym przez agenta w Windows | `GIT_OPTIONAL_LOCKS=0` dla komend odczytu; git wyłącznie z Windows (spod Linuksa pliki wyglądają na zmienione przez CRLF) |
| n8n 2.37 wymaga Node ≥ 24 | dotyczy tylko workflowów n8n, nie silnika |
| Pełny `pytest` trwa ~100 s | agent uruchamia go w tle z `--junitxml` |

## Co jest zrobione (nie buduj tego drugi raz)

- Pipeline `/upload` → `/run` → `/reports` z mapperem PL, metryki tydzień-do-tygodnia, alert n8n.
- Pipeline `uksc_evidence` z collectorem `tools/uksc-collector.ps1` i schematem `schemas/uksc_collector_v1.json`.
- DevSecOps: ruff, gitleaks, semgrep, zizmor, Dependabot, pre-commit, kontener bez roota.
- README ze screenshotem i wideo demo `docs/daas_demo.mp4`; materiały pilota w `docs/pilot/`.
- Poza repo: landing ofertowy na prochpc.pl i wzór umowy powierzenia danych (RODO art. 28).

*Aktualizacja: przy każdej zmianie stacku, zasad albo statusu.*

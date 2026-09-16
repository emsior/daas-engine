"""Z-10 — jedna kanoniczna sciezka audit.jsonl dla wszystkich call sites.

Dlug techniczny: sciezka logu audytowego byla skladana niezaleznie w trzech
miejscach (stala modulu policy, helper w routes, wyrazenie inline w runnerze).
Stala policy wskazywala w dodatku na inny katalog niz pozostale dwa, wiec kazde
wywolanie enforce() bez jawnego `audit_log` ladowalo w osobnym pliku.

Te testy pilnuja, ze:
    * zrodlem prawdy jest Settings.audit_log_path,
    * routes i runner pisza fizycznie do tego samego pliku,
    * enforce() bez jawnej sciezki trafia tam samo,
    * zapis jest append-only i odporny na rownolegle watki.

Dane wylacznie syntetyczne, generowane w locie. Bez sieci i bez sekretow.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import (
    AUDIT_LOG_FILENAME,
    PROJECT_ROOT,
    Settings,
    get_settings,
    reset_settings_cache,
)
from app.core.models import RunRequest
from app.core.policy import Budget, RunSQL, default_audit_log, enforce
from app.pipelines.runner import PipelineRunner
from app.storage.duckdb_client import DuckDBClient

HEADER = "Data,Kwota,Produkt\n"
ROW = "2026-03-01,100.00,Produkt testowy\n"

SECRET_ENV_KEYS = (
    "FACEIT_API_KEY",
    "LIQUIPEDIA_USER_AGENT",
    "APIFY_TOKEN",
    "DISCORD_WEBHOOK_URL",
    "ANTHROPIC_API_KEY",
)


def audit_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.fixture()
def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Izolowane wdrozenie z policy ON: wlasny runtime, wlasny klient HTTP, wlasny runner."""
    from app.main import create_app

    for key in SECRET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    reset_settings_cache()

    runtime_dir = tmp_path / "runtime"
    settings = Settings(
        _env_file=None,
        duckdb_path=str(runtime_dir / "t.duckdb"),
        reports_dir=str(runtime_dir / "reports"),
        fixtures_dir=str(PROJECT_ROOT / "data" / "fixtures"),
        daas_policy_enforce=True,
    )
    settings.ensure_dirs()
    uploads = settings.reports_path.parent / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    runner = PipelineRunner(settings=settings, db=DuckDBClient(settings.duckdb_file))
    app = create_app()
    with TestClient(app) as client:
        app.state.runner = runner
        yield {
            "client": client,
            "settings": settings,
            "runner": runner,
            "uploads": uploads,
            "tmp": tmp_path,
        }
    reset_settings_cache()


@pytest.fixture()
def canonical_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Konfiguracja globalna przestawiona na tmp_path — do testow default_audit_log()."""
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("DUCKDB_PATH", str(runtime_dir / "d.duckdb"))
    monkeypatch.setenv("REPORTS_DIR", str(runtime_dir / "reports"))
    for key in SECRET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    reset_settings_cache()
    try:
        yield runtime_dir
    finally:
        reset_settings_cache()


# ---------------------------------------------------------------------------
# Z10-01 — jedno zrodlo prawdy
# ---------------------------------------------------------------------------
def test_settings_is_single_source_of_truth(deployment):
    settings = deployment["settings"]

    assert settings.audit_log_path.name == AUDIT_LOG_FILENAME
    assert settings.audit_log_path == settings.duckdb_file.parent / AUDIT_LOG_FILENAME
    # Audyt mieszka w runtime tego wdrozenia, nie w katalogu projektu.
    assert settings.audit_log_path.parent == deployment["tmp"] / "runtime"


def test_policy_has_no_divergent_module_constant():
    """Regresja: stala AUDIT_LOG wskazywala na inny katalog niz Settings."""
    import app.core.policy as policy_mod

    assert not hasattr(policy_mod, "AUDIT_LOG"), (
        "wrocila stala modulu rozjezdzajaca sie ze sciezka kanoniczna"
    )
    assert callable(policy_mod.default_audit_log)


def test_no_call_site_builds_the_path_on_its_own():
    """Literal nazwy pliku ma zyc wylacznie w konfiguracji."""
    offenders = []
    for path in (PROJECT_ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if f'"{AUDIT_LOG_FILENAME}"' in text and path.name != "config.py":
            offenders.append(path.relative_to(PROJECT_ROOT).as_posix())
    assert offenders == [], f"literal sciezki audytu poza konfiguracja: {offenders}"


# ---------------------------------------------------------------------------
# Z10-02 — oba call sites pisza do jednego pliku
# ---------------------------------------------------------------------------
def test_routes_and_runner_write_to_the_same_file(deployment):
    settings = deployment["settings"]
    canonical = settings.audit_log_path

    source = deployment["uploads"] / "z10_source.csv"
    source.write_text(HEADER + ROW, encoding="utf-8")

    assert audit_lines(canonical) == []

    # --- call site 1: warstwa HTTP (routes.upload)
    response = deployment["client"].post(
        "/upload",
        files={"file": ("sprzedaz.csv", (HEADER + ROW * 5).encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 200, response.text
    after_routes = audit_lines(canonical)
    assert after_routes, "routes nie zapisalo do sciezki kanonicznej"

    # --- call site 2: orkiestrator (PipelineRunner.run)
    deployment["runner"].run(
        RunRequest(pipeline="ecommerce_demo", source_path=str(source))
    )
    after_runner = audit_lines(canonical)
    assert len(after_runner) > len(after_routes), "runner nie zapisalo do sciezki kanonicznej"

    # Oba zrodla sa obecne w jednym pliku: policy (tool/decision) i upload (action).
    assert any(rec.get("tool") == "read_file" for rec in after_runner)
    assert any("action" in rec for rec in after_runner)


def test_exactly_one_audit_file_exists_in_the_deployment(deployment):
    settings = deployment["settings"]
    source = deployment["uploads"] / "z10_source.csv"
    source.write_text(HEADER + ROW, encoding="utf-8")

    deployment["client"].post(
        "/upload",
        files={"file": ("sprzedaz.csv", (HEADER + ROW * 3).encode("utf-8"), "text/csv")},
    )
    deployment["runner"].run(
        RunRequest(pipeline="ecommerce_demo", source_path=str(source))
    )

    found = sorted(p.resolve() for p in deployment["tmp"].rglob(AUDIT_LOG_FILENAME))
    assert found == [settings.audit_log_path.resolve()], (
        f"log rozwarstwil sie na {len(found)} plikow"
    )


# ---------------------------------------------------------------------------
# Z10-03 — enforce() bez jawnej sciezki
# ---------------------------------------------------------------------------
def test_default_audit_log_follows_settings(canonical_env):
    assert default_audit_log() == get_settings().audit_log_path
    assert default_audit_log().parent == canonical_env


def test_enforce_without_explicit_path_lands_on_canonical(canonical_env):
    """Sedno Z-10: wywolanie bez `audit_log=` nie moze isc do wlasnego pliku."""
    canonical = get_settings().audit_log_path
    assert not canonical.exists()

    enforce(
        "run_sql",
        {"query": "SELECT 1"},
        RunSQL,
        lambda query: "wynik",
        Budget(),
        "analyst",
    )

    records = audit_lines(canonical)
    assert len(records) == 1
    assert records[0]["decision"] == "allow"
    assert records[0]["tool"] == "run_sql"

    # Stara sciezka (<projekt>/logs/audit.jsonl) nie moze powstac.
    assert not (PROJECT_ROOT / "logs" / AUDIT_LOG_FILENAME).exists()


# ---------------------------------------------------------------------------
# Z10-04 — append-only i wspolbieznosc
# ---------------------------------------------------------------------------
def test_audit_is_append_only(tmp_path: Path):
    log = tmp_path / AUDIT_LOG_FILENAME
    log.write_text('{"wczesniejszy_wpis": true}\n', encoding="utf-8")

    enforce(
        "run_sql", {"query": "SELECT 1"}, RunSQL, lambda query: 1,
        Budget(), "analyst", audit_log=log,
    )

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"wczesniejszy_wpis": True}, "nadpisano istniejacy audyt"


def test_concurrent_writers_produce_intact_lines(tmp_path: Path):
    """8 watkow x 20 wywolan -> 160 kompletnych linii JSON, zero sklejen."""
    log = tmp_path / AUDIT_LOG_FILENAME
    threads_count, per_thread = 8, 20
    errors: list[Exception] = []

    def worker(worker_id: int) -> None:
        try:
            budget = Budget(max_steps=per_thread)
            for i in range(per_thread):
                enforce(
                    "run_sql",
                    # Argumenty musza sie roznic — inaczej odpali detektor petli.
                    {"query": f"SELECT {worker_id} + {i}"},
                    RunSQL,
                    lambda query: "x" * 256,  # dluzszy wynik zwieksza szanse na przeplot
                    budget,
                    "analyst",
                    audit_log=log,
                )
        except Exception as exc:  # noqa: BLE001 - raportujemy do asercji
            errors.append(exc)

    workers = [threading.Thread(target=worker, args=(n,)) for n in range(threads_count)]
    for t in workers:
        t.start()
    for t in workers:
        t.join(timeout=30)

    assert errors == [], f"watek podniosl wyjatek: {errors[0]!r}"
    assert all(not t.is_alive() for t in workers), "watek nie zakonczyl sie w czasie"

    raw = log.read_text(encoding="utf-8").splitlines()
    assert len(raw) == threads_count * per_thread, "zgubione albo sklejone linie"
    for line in raw:
        json.loads(line)  # kazda linia jest kompletnym, samodzielnym rekordem


def test_concurrent_writers_from_both_modules_share_the_lock(tmp_path: Path):
    """Audyt policy i audyt uploadu ida tym samym kanalem zapisu."""
    from app.core.upload_guard import append_audit

    log = tmp_path / AUDIT_LOG_FILENAME
    errors: list[Exception] = []

    def policy_writer() -> None:
        try:
            budget = Budget(max_steps=30)
            for i in range(30):
                enforce(
                    "run_sql", {"query": f"SELECT {i}"}, RunSQL, lambda query: "p" * 128,
                    budget, "analyst", audit_log=log,
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def upload_writer() -> None:
        try:
            for i in range(30):
                append_audit({"action": "upload", "seq": i, "pad": "u" * 128}, log)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    workers = [threading.Thread(target=policy_writer), threading.Thread(target=upload_writer)]
    for t in workers:
        t.start()
    for t in workers:
        t.join(timeout=30)

    assert errors == [], f"watek podniosl wyjatek: {errors[0]!r}"
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 60
    assert sum(1 for r in records if r.get("tool") == "run_sql") == 30
    assert sum(1 for r in records if r.get("action") == "upload") == 30

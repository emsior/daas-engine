"""Limity zadan (token bucket w pamieci procesu), kwota uploadow i sprzatanie plikow *.tmp."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.ratelimit import RateLimiter
from app.core.upload_guard import ReasonCode, cleanup_stale_tmp
from app.main import create_app
from tests.conftest import API_KEY_HEADER, PROJECT_ROOT, TEST_API_KEY, auth_headers

CSV = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def limited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fabryka aplikacji z wlasnymi limitami i zegarem sterowanym przez test."""
    from contextlib import ExitStack

    stack = ExitStack()
    clock = FakeClock()

    def make(client_addr: tuple[str, int] = ("203.0.113.5", 5000), **overrides) -> tuple[TestClient, Settings]:
        s = Settings(_env_file=None, daas_api_key=TEST_API_KEY, duckdb_path=str(tmp_path / "d.duckdb"),
                     reports_dir=str(tmp_path / "reports"), fixtures_dir=str(PROJECT_ROOT / "data" / "fixtures"),
                     **overrides)
        s.ensure_dirs()
        app = create_app(s)
        c = stack.enter_context(TestClient(app, headers=auth_headers(), client=client_addr))
        app.state.limiter = RateLimiter(clock=clock)
        return c, s

    make.clock = clock
    yield make
    stack.close()


def _audit(s: Settings) -> list[dict]:
    p = s.audit_log_path
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


# ---------------------------------------------------------------- RateLimiter (jednostkowo)
def test_bucket_allows_a_burst_then_refills():
    clock = FakeClock()
    rl = RateLimiter(clock=clock)
    assert [rl.hit("r", "k", 3) for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = rl.hit("r", "k", 3)
    assert wait == pytest.approx(20.0)  # 3 na minute = 1 token na 20 s
    clock.advance(20)
    assert rl.hit("r", "k", 3) == 0.0
    assert rl.hit("r", "k", 3) > 0


def test_bucket_is_per_rule_and_per_key():
    rl = RateLimiter(clock=FakeClock())
    assert rl.hit("run", "a", 1) == 0.0 and rl.hit("run", "a", 1) > 0
    assert rl.hit("run", "b", 1) == 0.0   # inny klucz
    assert rl.hit("upload", "a", 1) == 0.0  # inna regula


def test_limit_zero_disables_the_rule():
    rl = RateLimiter(clock=FakeClock())
    assert all(rl.hit("r", "k", 0) == 0.0 for _ in range(1000))


def test_idle_buckets_are_pruned():
    clock = FakeClock()
    rl = RateLimiter(clock=clock, max_buckets=50)
    for i in range(50):
        rl.hit("r", f"ip-{i}", 5)
    clock.advance(600)
    rl.hit("r", "fresh", 5)
    assert rl.size() <= 2


def test_bucket_table_stays_bounded_under_many_keys():
    clock = FakeClock()
    rl = RateLimiter(clock=clock, max_buckets=100)
    for i in range(1000):
        rl.hit("r", f"ip-{i}", 5)
    assert rl.size() <= 100


# ---------------------------------------------------------------- HTTP: /run, /upload, IP
def test_run_returns_429_with_retry_after_after_the_limit(limited):
    c, _ = limited(daas_rate_limit_run_per_min=3)
    for _ in range(3):
        assert c.post("/run", json={"pipeline": "cs2_demo"}).status_code == 200
    r = c.post("/run", json={"pipeline": "cs2_demo"})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    body = r.json()
    assert body["code"] == "rate_limited" and body["request_id"] == r.headers["x-request-id"]
    limited.clock.advance(60)
    assert c.post("/run", json={"pipeline": "cs2_demo"}).status_code == 200


def test_upload_has_its_own_limit_independent_of_run(limited):
    c, _ = limited(daas_rate_limit_upload_per_min=2, daas_rate_limit_run_per_min=50)
    for _ in range(2):
        assert c.post("/upload", files={"file": ("a.csv", CSV, "text/csv")}).status_code == 200
    r = c.post("/upload", files={"file": ("a.csv", CSV, "text/csv")})
    assert r.status_code == 429 and "retry-after" in r.headers
    assert c.post("/run", json={"pipeline": "cs2_demo"}).status_code == 200


def test_rejected_upload_does_not_leave_files_behind_when_limited(limited):
    c, s = limited(daas_rate_limit_upload_per_min=1)
    c.post("/upload", files={"file": ("a.csv", CSV, "text/csv")})
    c.post("/upload", files={"file": ("a.csv", CSV, "text/csv")})
    up = s.reports_path.parent / "uploads"
    assert len(list(up.glob("*.csv"))) == 1 and list(up.glob("*.tmp")) == []


def test_per_ip_limit_covers_every_protected_route_but_not_health(limited):
    c, _ = limited(daas_rate_limit_ip_per_min=4)
    assert [c.get("/pipelines").status_code for _ in range(4)] == [200] * 4
    assert c.get("/pipelines").status_code == 429
    assert c.get("/runs").status_code == 429
    assert c.get("/health").status_code == 200 and c.get("/ready").status_code == 200


def test_per_ip_limit_is_separate_for_each_address(limited):
    a, _ = limited(("203.0.113.5", 1), daas_rate_limit_ip_per_min=2)
    for _ in range(2):
        assert a.get("/pipelines").status_code == 200
    assert a.get("/pipelines").status_code == 429
    # drugi adres, ta sama aplikacja i ten sam limiter
    b = TestClient(a.app, headers=auth_headers(), client=("203.0.113.9", 1))
    assert b.get("/pipelines").status_code == 200


def test_limits_apply_before_authentication_to_anonymous_floods(limited):
    c, _ = limited(daas_rate_limit_ip_per_min=3)
    anon = TestClient(c.app, client=("203.0.113.7", 1))
    codes = [anon.get("/pipelines").status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


def test_failed_keys_are_throttled_but_a_valid_key_still_works(limited):
    c, s = limited(daas_rate_limit_auth_fail_per_min=3, daas_rate_limit_ip_per_min=1000)
    bad = {API_KEY_HEADER: "zly-klucz-0123456789abcdef"}
    codes = [c.get("/pipelines", headers=bad).status_code for _ in range(6)]
    assert codes == [401, 401, 401, 429, 429, 429]
    assert c.get("/pipelines").status_code == 200  # poprawny klucz nie jest blokowany
    # audyt nie rosnie bez ograniczen: tylko pierwsze 3 odmowy, reszta (429) bez wpisu
    assert len([e for e in _audit(s) if e.get("action") == "auth_denied"]) == 3


def test_disabling_all_limits_with_zero(limited):
    c, _ = limited(daas_rate_limit_ip_per_min=0, daas_rate_limit_run_per_min=0,
                   daas_rate_limit_upload_per_min=0, daas_rate_limit_auth_fail_per_min=0)
    assert all(c.get("/pipelines").status_code == 200 for _ in range(50))


def test_defaults_are_enabled_and_sane():
    s = Settings(_env_file=None)
    assert 1 <= s.daas_rate_limit_run_per_min <= 600
    assert 1 <= s.daas_rate_limit_upload_per_min <= 600
    assert 1 <= s.daas_rate_limit_ip_per_min <= 6000
    assert 1 <= s.daas_rate_limit_auth_fail_per_min <= 600
    assert s.daas_upload_quota_bytes >= 10 * 1024 * 1024  # co najmniej jeden maksymalny upload


# ---------------------------------------------------------------- kwota uploadow
def test_upload_quota_blocks_when_storage_is_full(limited):
    c, s = limited(daas_upload_quota_bytes=len(CSV) * 2 + 10)
    up = s.reports_path.parent / "uploads"
    assert c.post("/upload", files={"file": ("a.csv", CSV, "text/csv")}).status_code == 200
    assert c.post("/upload", files={"file": ("b.csv", CSV, "text/csv")}).status_code == 200
    r = c.post("/upload", files={"file": ("c.csv", CSV, "text/csv")})
    assert r.status_code == 413
    assert r.json()["detail"] == "kwota uploadow wyczerpana" and r.json()["code"] == "payload_too_large"
    assert len(list(up.glob("*.csv"))) == 2 and list(up.glob("*.tmp")) == []
    last = [e for e in _audit(s) if e.get("action") == "upload"][-1]
    assert last["decision"] == "deny" and last["reason_code"] == ReasonCode.QUOTA_EXCEEDED
    # zwolnienie miejsca przywraca mozliwosc uploadu
    next(up.glob("*.csv")).unlink()
    assert c.post("/upload", files={"file": ("d.csv", CSV, "text/csv")}).status_code == 200


def test_upload_quota_counts_the_incoming_bytes_while_streaming(limited):
    c, s = limited(daas_upload_quota_bytes=len(CSV) - 1)
    assert c.post("/upload", files={"file": ("a.csv", CSV, "text/csv")}).status_code == 413
    assert list((s.reports_path.parent / "uploads").glob("*")) == []


# ---------------------------------------------------------------- sprzatanie *.tmp
def _make_tmp(directory: Path, name: str, age_sec: float) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_bytes(b"partial")
    past = time.time() - age_sec
    os.utime(p, (past, past))
    return p


STALE = "a" * 32 + ".tmp"
FRESH = "b" * 32 + ".tmp"


def test_cleanup_removes_only_stale_tmp_files_with_our_name_pattern(tmp_path: Path):
    stale = _make_tmp(tmp_path, STALE, 3600)
    fresh = _make_tmp(tmp_path, FRESH, 10)
    foreign = _make_tmp(tmp_path, "notes.tmp", 3600)       # nie nasz wzorzec
    final = _make_tmp(tmp_path, "c" * 32 + ".csv", 3600)   # plik finalny
    assert cleanup_stale_tmp(tmp_path, max_age_sec=900) == 1
    assert not stale.exists()
    assert fresh.exists() and foreign.exists() and final.exists()


def test_cleanup_with_zero_age_removes_every_matching_tmp(tmp_path: Path):
    _make_tmp(tmp_path, STALE, 5)
    _make_tmp(tmp_path, FRESH, 0)
    assert cleanup_stale_tmp(tmp_path, max_age_sec=0) == 2


def test_cleanup_on_a_missing_directory_is_a_noop(tmp_path: Path):
    assert cleanup_stale_tmp(tmp_path / "nie-ma", max_age_sec=0) == 0


def test_upload_cleans_stale_tmp_files_of_crashed_requests(limited):
    c, s = limited()
    up = s.reports_path.parent / "uploads"
    stale = _make_tmp(up, STALE, 3600)
    fresh = _make_tmp(up, FRESH, 5)
    assert c.post("/upload", files={"file": ("a.csv", CSV, "text/csv")}).status_code == 200
    assert not stale.exists() and fresh.exists()


def test_startup_removes_leftover_tmp_files(tmp_path: Path):
    s = Settings(_env_file=None, daas_api_key=TEST_API_KEY, duckdb_path=str(tmp_path / "d.duckdb"),
                 reports_dir=str(tmp_path / "reports"), fixtures_dir=str(PROJECT_ROOT / "data" / "fixtures"))
    s.ensure_dirs()
    leftover = _make_tmp(s.reports_path.parent / "uploads", FRESH, 0)
    with TestClient(create_app(s)):
        pass
    assert not leftover.exists()


def test_stale_tmp_does_not_count_against_the_quota(limited):
    c, s = limited(daas_upload_quota_bytes=len(CSV) + 100)
    _make_tmp(s.reports_path.parent / "uploads", STALE, 3600)
    assert c.post("/upload", files={"file": ("a.csv", CSV, "text/csv")}).status_code == 200

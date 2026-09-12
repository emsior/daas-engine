"""Testy policy layer: allowlista rol, kontrakty narzedzi, budzety, HITL, audit log."""
from __future__ import annotations

import json

import pytest

from app.core.policy import Budget, PolicyError, ReadFile, RunSQL, enforce


@pytest.fixture()
def log(tmp_path):
    return tmp_path / "audit.jsonl"


def _noop(**kwargs):
    return "wynik"


def test_allowlista_blokuje_narzedzie_spoza_roli(log):
    with pytest.raises(PolicyError, match="uprawnien"):
        enforce("git_push", {}, ReadFile, _noop, Budget(), "analyst", audit_log=log)


def test_blokada_sciezki_poza_workspace(log):
    with pytest.raises(PolicyError):
        enforce("read_file", {"path": "../../../etc/passwd"}, ReadFile, _noop,
                Budget(), "analyst", audit_log=log)


def test_sql_tylko_do_odczytu(log):
    with pytest.raises(PolicyError):
        enforce("run_sql", {"query": "DROP TABLE raporty"}, RunSQL, _noop,
                Budget(), "analyst", audit_log=log)


def test_sql_select_przechodzi(log):
    assert enforce("run_sql", {"query": "SELECT 1"}, RunSQL, lambda query: "wynik",
                   Budget(), "analyst", audit_log=log) == "wynik"


def test_operacja_nieodwracalna_wymaga_zgody(log):
    with pytest.raises(PolicyError, match="zgody"):
        enforce("send_email", {"path": "raport.html"}, ReadFile, _noop,
                Budget(), "executor", audit_log=log)


def test_operacja_nieodwracalna_przechodzi_po_zgodzie(log):
    result = enforce("send_email", {"path": "raport.html"}, ReadFile,
                     lambda path: "wyslano", Budget(), "executor",
                     approve=lambda tool, args: True, audit_log=log)
    assert result == "wyslano"


def test_detektor_petli(log):
    budget = Budget()
    with pytest.raises(PolicyError, match="petla"):
        for _ in range(3):
            enforce("run_sql", {"query": "SELECT 1"}, RunSQL, lambda query: 1,
                    budget, "analyst", audit_log=log)


def test_limit_krokow(log):
    budget = Budget(max_steps=1)
    enforce("run_sql", {"query": "SELECT 1"}, RunSQL, lambda query: 1,
            budget, "analyst", audit_log=log)
    with pytest.raises(PolicyError, match="krokow"):
        enforce("run_sql", {"query": "SELECT 2"}, RunSQL, lambda query: 1,
                budget, "analyst", audit_log=log)


def test_audit_loguje_allow_i_deny(log):
    enforce("run_sql", {"query": "SELECT 1"}, RunSQL, lambda query: 1,
            Budget(), "analyst", audit_log=log)
    with pytest.raises(PolicyError):
        enforce("git_push", {}, ReadFile, _noop, Budget(), "analyst", audit_log=log)

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert any(r["decision"] == "allow" for r in rows)
    assert any(r["decision"].startswith("deny") for r in rows)
    assert all({"ts", "role", "tool", "args", "decision", "result_sha"} <= r.keys() for r in rows)

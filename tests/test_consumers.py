"""Konsumenci API (n8n, CI, skrypt startowy) wysylaja klucz tam, gdzie trasa go wymaga."""
from __future__ import annotations

import json
import re

import pytest

from tests.conftest import PROJECT_ROOT

N8N_FILES = sorted((PROJECT_ROOT / "n8n").glob("*.json"))
PROTECTED_CALL = re.compile(r"/(run|upload)\b")
PUBLIC_CALL = re.compile(r"/(health|ready)\b")


def _http_nodes(path):
    wf = json.loads(path.read_text(encoding="utf-8"))
    return [n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"]


def test_n8n_workflows_are_present():
    assert [p.name for p in N8N_FILES] == ["daas_client_report.json", "daas_workflow.json", "pilot_workflow.json"]


@pytest.mark.parametrize("path", N8N_FILES, ids=lambda p: p.name)
def test_n8n_calls_to_protected_routes_send_the_api_key(path):
    calls = [n for n in _http_nodes(path) if PROTECTED_CALL.search(n["parameters"]["url"])]
    assert calls, "workflow bez wywolan /run lub /upload?"
    for node in calls:
        params = node["parameters"]
        assert params.get("sendHeaders") is True, node["name"]
        headers = {h["name"]: h["value"] for h in params["headerParameters"]["parameters"]}
        assert headers == {"X-API-Key": "={{ $env.DAAS_API_KEY }}"}, node["name"]


@pytest.mark.parametrize("path", N8N_FILES, ids=lambda p: p.name)
def test_n8n_public_health_probe_needs_no_key_and_keys_are_not_inlined(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"X-API-Key\"\s*,\s*\"value\"\s*:\s*\"(?!=\{\{)", text)  # tylko wyrazenie z env
    for node in _http_nodes(path):
        if PUBLIC_CALL.search(node["parameters"]["url"]):
            assert "headerParameters" not in node["parameters"]


def test_n8n_client_workflow_posts_upload_id_payload():
    text = (PROJECT_ROOT / "n8n" / "daas_client_report.json").read_text(encoding="utf-8")
    assert "run_payload" in text and "source_path" not in text


def test_ci_smoke_uses_a_key_and_checks_401():
    ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.count("X-API-Key") >= 5
    assert ci.count('= "401"') == 2  # test negatywny w smoke API i w smoke kontenera
    assert "openssl rand" in ci


def test_powershell_launcher_sends_the_key():
    ps1 = (PROJECT_ROOT / "URUCHOM_DAAS_PYTHON.ps1").read_text(encoding="utf-8")
    assert "X-API-Key" in ps1 and "DAAS_API_KEY" in ps1
    assert "--host 127.0.0.1" in ps1

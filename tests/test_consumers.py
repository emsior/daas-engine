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


# ---------------------------------------------------------------- Docker / compose (H2)
def test_dockerfile_takes_host_from_config_not_from_cmd():
    df = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    cmd = next(line for line in df.splitlines() if line.startswith("CMD"))
    assert "--host" not in cmd and "0.0.0.0" not in cmd  # noqa: S104
    assert "APP_HOST=0.0.0.0" in df  # noqa: S104  (tylko w obrazie, jawnie)
    assert "APP_ENV=prod" in df
    assert "/health" in df  # HEALTHCHECK na publicznym /health


def test_compose_publishes_ports_on_loopback_only_and_requires_the_key():
    import yaml

    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, svc in compose["services"].items():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), (name, port)
    for name in ("app", "n8n"):
        assert "DAAS_API_KEY" in compose["services"][name]["environment"]
        assert ":?" in compose["services"][name]["environment"]["DAAS_API_KEY"]


# ---------------------------------------------------------------- zaleznosci przypiete hashami (H11)
LOCKS = ("requirements.lock", "requirements-dev.lock")


@pytest.mark.parametrize("name", LOCKS)
def test_lock_files_pin_every_package_with_hashes(name):
    text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
    entries = re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", text, flags=re.M)
    assert len(entries) > 10
    blocks = re.split(r"^(?=[A-Za-z0-9_.\-]+==)", text, flags=re.M)[1:]
    assert len(blocks) == len(entries)
    for block in blocks:
        assert "--hash=sha256:" in block, block.splitlines()[0]
    names = {n.lower() for n, _ in entries}
    assert {"fastapi", "duckdb", "openpyxl", "pandas"} <= names


def test_dev_lock_includes_pinned_ruff_and_runtime_lock_does_not():
    runtime = (PROJECT_ROOT / "requirements.lock").read_text(encoding="utf-8")
    dev = (PROJECT_ROOT / "requirements-dev.lock").read_text(encoding="utf-8")
    assert "\nruff==0.16.8" in dev
    assert "\nruff==" not in runtime and "\npytest==" not in runtime


def test_dockerfile_and_ci_install_from_hash_locked_files():
    df = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements.lock" in df and "--require-hashes" in df
    assert "pip install --upgrade pip" not in df and 'pip==' in df
    ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--require-hashes -r requirements-dev.lock" in ci


def test_n8n_image_is_pinned_to_an_exact_version():
    import yaml

    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["n8n"]["image"] == "n8nio/n8n:2.43.1"

"""`docs/openapi.json` jest generowany z aplikacji (tryb cloud) i musi byc aktualny w repo."""
from __future__ import annotations

import json
import subprocess
import sys

from tests.conftest import PROJECT_ROOT

OPENAPI_FILE = PROJECT_ROOT / "docs" / "openapi.json"


def test_openapi_file_in_repo_is_up_to_date():
    from tools.export_openapi import render

    assert OPENAPI_FILE.is_file(), "brak docs/openapi.json: uruchom `python tools/export_openapi.py`"
    assert OPENAPI_FILE.read_text(encoding="utf-8") == render(), (
        "docs/openapi.json jest nieaktualny: uruchom `python tools/export_openapi.py`")


def test_rendering_is_deterministic_and_independent_of_the_environment(monkeypatch):
    from tools.export_openapi import render

    first = render()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DAAS_MODE", "selfhost")
    monkeypatch.setenv("DAAS_COCKPIT_ORIGIN", "https://elsewhere.example.test")
    assert render() == first


def test_document_describes_the_cloud_api():
    doc = json.loads(OPENAPI_FILE.read_text(encoding="utf-8"))
    assert doc["openapi"].startswith("3.")
    assert doc["info"]["version"] == "2.0.0"
    paths = doc["paths"]
    for expected in ("/api/v1/me", "/api/v1/orgs/{org_id}/jobs", "/api/v1/orgs/{org_id}/usage",
                     "/api/v1/orgs/{org_id}/audit", "/api/v1/orgs/{org_id}/webhooks",
                     "/api/v1/orgs/{org_id}/webhooks/{webhook_id}/ping", "/api/v1/orgs/{org_id}/verify",
                     "/metrics"):
        assert expected in paths, expected
    # tryb cloud: trasy legacy nie istnieja
    assert not {"/run", "/upload", "/pipelines"} & set(paths)
    assert not any(p.startswith("/reports") for p in paths)


def test_document_has_no_local_paths_secrets_or_owner_data():
    text = OPENAPI_FILE.read_text(encoding="utf-8")
    for forbidden in (str(PROJECT_ROOT), "/home/", "/tmp/", "mcq" + "089", "test-key-", "whsec_"):
        assert forbidden not in text, forbidden


def test_every_operation_has_a_summary_and_tags():
    doc = json.loads(OPENAPI_FILE.read_text(encoding="utf-8"))
    for path, ops in doc["paths"].items():
        for method, op in ops.items():
            assert op.get("summary"), f"{method.upper()} {path}: brak summary"
            assert op.get("tags"), f"{method.upper()} {path}: brak tagow"


def test_cli_check_passes_for_the_committed_file():
    script = str(PROJECT_ROOT / "tools" / "export_openapi.py")
    done = subprocess.run([sys.executable, script, "--check"],  # noqa: S603
                          cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr

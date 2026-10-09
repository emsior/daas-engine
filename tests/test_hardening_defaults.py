"""Bezpieczne wartosci domyslne: bind, policy, wersja, srodowisko."""
from __future__ import annotations

import tomllib
from importlib import metadata

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.conftest import PROJECT_ROOT

PYPROJECT_VERSION = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_default_bind_is_loopback():
    assert Settings(_env_file=None).app_host == "127.0.0.1"


def test_policy_is_enforced_by_default():
    assert Settings(_env_file=None).daas_policy_enforce is True


def test_policy_can_be_disabled_explicitly_via_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DAAS_POLICY_ENFORCE", "false")
    assert Settings(_env_file=None).daas_policy_enforce is False


def test_version_is_2_0_0_everywhere():
    assert PYPROJECT_VERSION == "2.0.0"
    assert Settings(_env_file=None).app_version == PYPROJECT_VERSION


def test_installed_metadata_matches_pyproject():
    # po zmianie wersji w pyproject.toml: pip install -e . (odswieza metadane pakietu)
    assert metadata.version("daas-engine") == PYPROJECT_VERSION


def test_health_version_comes_from_package_metadata(anon_client: TestClient):
    assert anon_client.get("/health").json()["version"] == metadata.version("daas-engine")


def test_health_version_falls_back_to_settings_without_metadata(anon_client: TestClient,
                                                                monkeypatch: pytest.MonkeyPatch):
    def missing(_name):
        raise metadata.PackageNotFoundError

    monkeypatch.setattr("app.core.config.metadata.version", missing)
    assert anon_client.get("/health").json()["version"] == Settings(_env_file=None).app_version


def test_openapi_version_matches_health(monkeypatch: pytest.MonkeyPatch):
    from app.main import create_app

    monkeypatch.setenv("APP_ENV", "dev")
    from app.core.config import reset_settings_cache

    reset_settings_cache()
    with TestClient(create_app()) as c:
        assert c.get("/openapi.json").json()["info"]["version"] == c.get("/health").json()["version"]


# ---------------------------------------------------------------- .env.example zgodny z Settings
def test_env_example_lists_every_hardening_setting_with_safe_defaults():
    from app.core.config import Settings
    from tests.conftest import PROJECT_ROOT

    lines = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    values = {}
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()

    for name in ("DAAS_API_KEY", "DAAS_LLM_NARRATIVE", "DAAS_POLICY_ENFORCE", "DAAS_UPLOAD_QUOTA_BYTES",
                 "DAAS_RATE_LIMIT_RUN_PER_MIN", "DAAS_RATE_LIMIT_UPLOAD_PER_MIN",
                 "DAAS_RATE_LIMIT_IP_PER_MIN", "DAAS_RATE_LIMIT_AUTH_FAIL_PER_MIN"):
        assert name in values, name
    s = Settings(_env_file=None)
    assert values["APP_ENV"] == s.app_env == "prod"
    assert values["APP_HOST"] == s.app_host == "127.0.0.1"
    assert values["DAAS_POLICY_ENFORCE"] == "true" and s.daas_policy_enforce is True
    assert values["DAAS_LLM_NARRATIVE"] == "false" and s.daas_llm_narrative is False
    assert int(values["DAAS_UPLOAD_QUOTA_BYTES"]) == s.daas_upload_quota_bytes
    assert int(values["DAAS_RATE_LIMIT_RUN_PER_MIN"]) == s.daas_rate_limit_run_per_min
    assert int(values["DAAS_RATE_LIMIT_UPLOAD_PER_MIN"]) == s.daas_rate_limit_upload_per_min
    assert int(values["DAAS_RATE_LIMIT_IP_PER_MIN"]) == s.daas_rate_limit_ip_per_min
    assert int(values["DAAS_RATE_LIMIT_AUTH_FAIL_PER_MIN"]) == s.daas_rate_limit_auth_fail_per_min
    assert values["DAAS_API_KEY"] == ""  # w szablonie nigdy realny klucz

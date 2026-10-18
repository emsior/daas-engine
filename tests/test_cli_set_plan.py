"""CLI `set-plan` (F4, A09 S-05-01): operatorowa sciezka zmiany planu organizacji + audyt, walidacja planu i org.

Dotad nie bylo zadnej sciezki zmiany planu (PATCH /orgs ignorowal `plan`, brak komendy CLI). `set-plan` to
jedyna, wiazaca sciezka operatorowa w 2.0.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from app.cli import main as cli_main
from app.control import models
from app.control.db import create_engine, make_sessionmaker
from app.core.config import Settings, reset_settings_cache


def _url(db_file: Path) -> str:
    return f"sqlite+aiosqlite:///{db_file}"


def _run(coro):
    return asyncio.run(coro)


async def _with_session(settings: Settings, fn):
    engine = create_engine(settings)
    try:
        async with make_sessionmaker(engine)() as db:
            return await fn(db)
    finally:
        await engine.dispose()


def _create_org(capsys, name: str = "Acme") -> str:
    assert cli_main(["create-org", "--name", name, "--owner-email", "boss@example.test"]) == 0
    out = capsys.readouterr().out
    return dict(line.split(":", 1) for line in out.strip().splitlines())["org_id"].strip()


@pytest.fixture()
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Wlasna baza control plane dla CLI (nadpisuje baze autouse z conftestu)."""
    db_file = tmp_path / "cli.db"
    monkeypatch.setenv("DAAS_CONTROL_DB_URL", _url(db_file))
    reset_settings_cache()
    return Settings(_env_file=None, daas_control_db_url=_url(db_file))


def test_set_plan_changes_free_to_pro_and_audits(cli_db: Settings, capsys):
    org_id = _create_org(capsys)

    assert cli_main(["set-plan", "--org", org_id, "--plan", "pro"]) == 0
    out = capsys.readouterr().out
    assert org_id in out and "pro" in out

    async def go(db):
        org = await db.get(models.Org, org_id)
        events = list((await db.execute(select(models.AuditEvent).where(
            models.AuditEvent.action == "org.updated"))).scalars())
        return org.plan, events

    plan, events = _run(_with_session(cli_db, go))
    assert plan == "pro"
    assert any(e.org_id == org_id and e.meta.get("plan") == "pro" for e in events)


def test_set_plan_accepts_every_plan_from_plans_module(cli_db: Settings, capsys):
    from app.control.plans import PLAN_LIMITS

    org_id = _create_org(capsys)
    for plan in sorted(PLAN_LIMITS):
        assert cli_main(["set-plan", "--org", org_id, "--plan", plan]) == 0
        capsys.readouterr()

    async def go(db):
        return (await db.get(models.Org, org_id)).plan

    assert _run(_with_session(cli_db, go)) == sorted(PLAN_LIMITS)[-1]


def test_set_plan_rejects_unknown_plan_without_changing_it(cli_db: Settings, capsys):
    org_id = _create_org(capsys)
    assert cli_main(["set-plan", "--org", org_id, "--plan", "ultra"]) == 2
    assert "unknown plan" in capsys.readouterr().err

    async def go(db):
        return (await db.get(models.Org, org_id)).plan

    assert _run(_with_session(cli_db, go)) == "free"


def test_set_plan_rejects_unknown_org(cli_db: Settings, capsys):
    assert cli_main(["set-plan", "--org", "01NOSUCHORG0000000000000000", "--plan", "pro"]) == 1
    assert "not found" in capsys.readouterr().err

"""Polecenia operatora: `python -m app.cli <migrate|migrate-runtime|create-org|purge>`.

    migrate           alembic upgrade head dla bazy control plane (idempotentne)
    migrate-runtime   przeniesienie runtime/ sprzed 2.0 do runtime/tenants/default/ (idempotentne)
    create-org        nowa organizacja z kontem wlasciciela i jednorazowym kluczem API
    purge             retencja: kasowanie uploadow, raportow i danych najemcow po terminie (--dry-run, --org)
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import Settings, get_settings


def cmd_migrate(settings: Settings, _args: argparse.Namespace) -> int:
    from app.control.db import resolve_db_url, upgrade_sync

    upgrade_sync(settings)
    print(f"control database is at head: {_redact_url(resolve_db_url(settings))}")
    return 0


def _redact_url(url: str) -> str:
    from sqlalchemy import make_url

    return make_url(url).render_as_string(hide_password=True)


async def _create_org(settings: Settings, name: str, owner_email: str, locale: str) -> tuple[str, str, str]:
    from app.control import scopes as sc
    from app.control import service
    from app.control.db import create_engine, make_sessionmaker

    email = service.normalize_email(owner_email)
    if not service.is_valid_email(email):
        raise SystemExit("error: --owner-email is not a valid e-mail address")
    if not name.strip() or len(name.strip()) > 200:
        raise SystemExit("error: --name must have 1-200 characters")
    engine = create_engine(settings)
    try:
        async with make_sessionmaker(engine)() as db:
            owner, _ = await service.get_or_create_user(db, email, locale=locale)
            org = await service.create_org(
                db, name=name, owner=owner, locale=locale, retention_days=settings.daas_retention_days)
            key, raw = await service.issue_api_key(
                db, org_id=org.id, name="initial", scopes=list(sc.KEY_SCOPES), created_by=owner.id)
            await db.commit()
            return org.id, key.prefix, raw
    finally:
        await engine.dispose()


def cmd_create_org(settings: Settings, args: argparse.Namespace) -> int:
    from app.control.db import upgrade_sync

    upgrade_sync(settings)
    org_id, prefix, raw = asyncio.run(_create_org(settings, args.name, args.owner_email, args.locale))
    print(f"org_id:  {org_id}")
    print(f"api_key: {raw}")
    print(f"prefix:  {prefix}")
    print("The API key is shown once and is not stored; keep it in a secret manager.", file=sys.stderr)
    return 0


def cmd_migrate_runtime(settings: Settings, args: argparse.Namespace) -> int:
    from app.control.migrate_runtime import migrate_runtime

    report = migrate_runtime(settings, dry_run=args.dry_run)
    for line in report.lines():
        print(line)
    return 1 if report.conflicts else 0


async def _purge(settings: Settings, org_id: str | None, dry_run: bool):
    from app.control.db import create_engine, make_sessionmaker
    from app.control.retention import purge_all
    from app.control.tenancy import TenantRegistry

    engine = create_engine(settings)
    registry = TenantRegistry(settings)
    try:
        return await purge_all(make_sessionmaker(engine), registry, settings, org_id=org_id, dry_run=dry_run)
    finally:
        registry.close_all()
        await engine.dispose()


def cmd_purge(settings: Settings, args: argparse.Namespace) -> int:
    from app.control.db import upgrade_sync

    upgrade_sync(settings)
    reports = asyncio.run(_purge(settings, args.org, args.dry_run))
    if args.org and not reports:
        print(f"error: organization {args.org} not found", file=sys.stderr)
        return 1
    for report in reports:
        print(report.line())
    print(f"{'dry-run: nothing was deleted; ' if args.dry_run else ''}organizations processed: {len(reports)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply control database migrations").set_defaults(func=cmd_migrate)
    mr = sub.add_parser("migrate-runtime", help="move pre-2.0 runtime files to runtime/tenants/default/")
    mr.add_argument("--dry-run", action="store_true", help="print what would be moved, change nothing")
    mr.set_defaults(func=cmd_migrate_runtime)
    pu = sub.add_parser("purge", help="apply data retention (delete expired uploads, reports and tenant rows)")
    pu.add_argument("--dry-run", action="store_true", help="count what would be deleted, delete nothing")
    pu.add_argument("--org", help="limit to one organization id")
    pu.set_defaults(func=cmd_purge)
    co = sub.add_parser("create-org", help="create an organization and print a one-time API key")
    co.add_argument("--name", required=True)
    co.add_argument("--owner-email", required=True)
    co.add_argument("--locale", default="pl", choices=["pl", "en"])
    co.set_defaults(func=cmd_create_org)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(get_settings(), args))


if __name__ == "__main__":
    raise SystemExit(main())

"""Control plane on a real PostgreSQL: row locks (`FOR UPDATE`), SAVEPOINT upsert and Alembic migrations.

SQLite ignores `FOR UPDATE` and serialises writers, so none of this is observable there. The module runs only
against a server given in `DAAS_TEST_POSTGRES_URL` (`postgresql+asyncpg://...`, a role with CREATEDB). Every test
gets a fresh database (`daas_t_<hex>`), migrated by Alembic and dropped afterwards.
With `DAAS_TEST_POSTGRES_REQUIRED=1` (CI) a missing URL or driver is an error, never a silent skip.

Races are interleaved deterministically: session 1 keeps its transaction open, session 2 runs as a task, and the
test proceeds only once the server itself reports (`pg_blocking_pids`) that session 2 waits for session 1.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import importlib.util
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from mcp.shared.auth import OAuthClientInformationFull
from mcp.shared.auth import OAuthToken as TokenResponse
from pydantic import AnyHttpUrl, AnyUrl
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.control import models, oauth, service
from app.control.db import (
    alembic_config,
    create_engine,
    make_sessionmaker,
    resolve_db_url,
    upgrade_sync,
)
from app.control.models import Base, Membership, UsageCounter
from app.control.security import sha256_hex
from app.control.types import utcnow
from app.core.config import Settings
from app.core.ratelimit import RateLimiter
from app.mcp_server.gateway import McpGateway
from tests.oauth_helpers import ISSUER, REDIRECT, RESOURCE, pkce_pair

PG_URL = os.environ.get("DAAS_TEST_POSTGRES_URL", "").strip()
PG_REQUIRED = os.environ.get("DAAS_TEST_POSTGRES_REQUIRED") == "1"
_EXPECTED_TABLES = {
    "orgs", "users", "memberships", "api_keys", "magic_links", "sessions", "invitations", "jobs",
    "uploads", "run_records", "audit_events", "usage_counters", "webhooks", "oauth_grants",
}
_BLOCK_TIMEOUT_S = 10.0
_NO_BLOCK_TIMEOUT_S = 5.0  # negative control: session 2 must finish on its own within this time
_POLL_STEP_S = 0.02


def _unavailable(reason: str) -> None:
    if PG_REQUIRED:
        pytest.fail(f"DAAS_TEST_POSTGRES_REQUIRED=1 but {reason}", pytrace=False)
    pytest.skip(reason, allow_module_level=True)


if not PG_URL:
    _unavailable("DAAS_TEST_POSTGRES_URL is not set")
if importlib.util.find_spec("asyncpg") is None:
    _unavailable("asyncpg is not installed (pip install -r requirements-postgres.lock)")


# ---------------------------------------------------------------- fresh database per test
def _run_admin(sql: str) -> None:
    """One statement on the server database from the URL, AUTOCOMMIT (CREATE/DROP DATABASE forbid a transaction)."""
    async def go() -> None:
        engine = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(sql))
        finally:
            await engine.dispose()

    asyncio.run(go())


@pytest.fixture()
def pg_settings() -> Iterator[Settings]:
    """Settings pointing at an empty, freshly created database (dropped with FORCE afterwards)."""
    name = f"daas_t_{uuid.uuid4().hex[:12]}"  # hex only: safe to put into DDL
    _run_admin(f"CREATE DATABASE {name}")  # noqa: S608
    try:
        url = make_url(PG_URL).set(database=name).render_as_string(hide_password=False)
        yield Settings(_env_file=None, daas_control_db_url=url)
    finally:
        _run_admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")  # noqa: S608


@pytest.fixture()
def migrated(pg_settings: Settings) -> Settings:
    upgrade_sync(pg_settings)  # outside any running event loop (env.py starts its own)
    return pg_settings


# ---------------------------------------------------------------- migrations
async def _schema_state(settings: Settings) -> tuple[set[str], list]:
    """Table names and the Alembic autogenerate diff between the live schema and the models."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def inspect_sync(sync_conn):
        ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})
        return set(inspect(sync_conn).get_table_names()), compare_metadata(ctx, Base.metadata)

    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(inspect_sync)
    finally:
        await engine.dispose()


def test_migrations_up_down_up_match_models(pg_settings: Settings):
    from alembic import command

    upgrade_sync(pg_settings)
    tables, diff = asyncio.run(_schema_state(pg_settings))
    assert _EXPECTED_TABLES <= tables
    assert diff == []  # models and migrations agree on PostgreSQL too

    command.downgrade(alembic_config(resolve_db_url(pg_settings)), "base")
    tables, _ = asyncio.run(_schema_state(pg_settings))
    assert tables <= {"alembic_version"}

    upgrade_sync(pg_settings)
    tables, diff = asyncio.run(_schema_state(pg_settings))
    assert _EXPECTED_TABLES <= tables
    assert diff == []


# ---------------------------------------------------------------- two-session harness
@dataclass
class Pair:
    maker: async_sessionmaker[AsyncSession]
    s1: AsyncSession
    s2: AsyncSession
    pid1: int
    pid2: int
    monitor: AsyncConnection  # separate AUTOCOMMIT connection: observes the server, takes no part in the race


async def _backend_pid(session: AsyncSession) -> int:
    # Starts the session's transaction, so the pid stays valid until commit/rollback.
    return (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()


@asynccontextmanager
async def _pair(settings: Settings) -> AsyncIterator[Pair]:
    engine = create_engine(settings)
    monitor_engine = create_async_engine(resolve_db_url(settings), isolation_level="AUTOCOMMIT")
    maker = make_sessionmaker(engine)
    try:
        async with monitor_engine.connect() as monitor, maker() as s1, maker() as s2:
            yield Pair(maker, s1, s2, await _backend_pid(s1), await _backend_pid(s2), monitor)
    finally:
        await monitor_engine.dispose()
        await engine.dispose()


async def _wait_until_blocked(pair: Pair, task: asyncio.Task) -> None:
    """Returns once the server reports that session 2 waits for session 1; fails if it finishes or never waits."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _BLOCK_TIMEOUT_S
    while loop.time() < deadline:
        if task.done():
            task.result()  # surface an exception from the task, if any
            pytest.fail("session 2 finished without waiting for session 1's lock")
        blocked = (await pair.monitor.execute(
            text("SELECT :blocker = ANY(pg_blocking_pids(:waiter))"),
            {"blocker": pair.pid1, "waiter": pair.pid2})).scalar_one()
        if blocked:
            return
        await asyncio.sleep(_POLL_STEP_S)  # poll step of the server-side check, not a synchronisation sleep
    pytest.fail(f"session 2 was not blocked by session 1 within {_BLOCK_TIMEOUT_S:.0f} s")


async def _interleave(
    settings: Settings,
    first: Callable[[AsyncSession], Awaitable[Any]],
    second: Callable[[AsyncSession], Awaitable[Any]],
    *,
    expect_block: bool,
) -> tuple[Any, Any]:
    """`first` runs on session 1 and stays uncommitted; `second` runs as a task on session 2.

    expect_block=True: assert (via the server) that session 2 waits, then commit 1, await 2, commit 2.
    expect_block=False: session 2 must finish by itself before session 1 commits.
    """
    async with _pair(settings) as pair:
        r1 = await first(pair.s1)
        task = asyncio.create_task(second(pair.s2))
        try:
            if expect_block:
                await _wait_until_blocked(pair, task)
                await pair.s1.commit()
                r2 = await asyncio.wait_for(task, _BLOCK_TIMEOUT_S)
            else:
                try:
                    r2 = await asyncio.wait_for(asyncio.shield(task), _NO_BLOCK_TIMEOUT_S)
                except TimeoutError:
                    pytest.fail(
                        f"session 2 waited {_NO_BLOCK_TIMEOUT_S:.0f} s although the row lock was disabled "
                        "(it should not have been blocked by session 1)")
                await pair.s1.commit()
            await pair.s2.commit()
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(BaseException):
                await task
        return r1, r2


async def _seed_two_owners(settings: Settings) -> tuple[str, str, str]:
    """Org with owners A and B. Returns `(org_id, user_a, user_b)`."""
    engine = create_engine(settings)
    try:
        async with make_sessionmaker(engine)() as db:
            a, _ = await service.get_or_create_user(db, "a@example.test")
            b, _ = await service.get_or_create_user(db, "b@example.test")
            org = await service.create_org(db, name="Acme", owner=a)
            db.add(Membership(org_id=org.id, user_id=b.id, role="owner"))
            await db.commit()
            return org.id, a.id, b.id
    finally:
        await engine.dispose()


async def _roles(settings: Settings, org_id: str) -> dict[str, str]:
    engine = create_engine(settings)
    try:
        async with make_sessionmaker(engine)() as db:
            rows = await db.execute(select(Membership.user_id, Membership.role).where(Membership.org_id == org_id))
            return {user_id: role for user_id, role in rows.all()}
    finally:
        await engine.dispose()


def _owners(roles: dict[str, str]) -> list[str]:
    return sorted(user_id for user_id, role in roles.items() if role == "owner")


# ---------------------------------------------------------------- "at least one owner" under READ COMMITTED
def test_concurrent_owner_deletes_leave_one_owner(migrated: Settings):
    async def go():
        org, a, b = await _seed_two_owners(migrated)
        r1, r2 = await _interleave(
            migrated,
            lambda s1: service.delete_member_guarded(s1, org, a),
            lambda s2: service.delete_member_guarded(s2, org, b),
            expect_block=True)
        return r1, r2, await _roles(migrated, org), b

    r1, r2, roles, b = asyncio.run(go())
    assert (r1, r2) == (True, False)
    assert _owners(roles) == [b]


def test_concurrent_owner_demotions_leave_one_owner(migrated: Settings):
    async def go():
        org, a, b = await _seed_two_owners(migrated)
        r1, r2 = await _interleave(
            migrated,
            lambda s1: service.set_member_role_guarded(s1, org, a, "member"),
            lambda s2: service.set_member_role_guarded(s2, org, b, "admin"),
            expect_block=True)
        return r1, r2, await _roles(migrated, org), a, b

    r1, r2, roles, a, b = asyncio.run(go())
    assert (r1, r2) == (True, False)
    assert _owners(roles) == [b]
    assert roles[a] == "member"


def test_race_is_real_without_the_row_lock(migrated: Settings, monkeypatch: pytest.MonkeyPatch):
    """Harness guard: with `_lock_owner_rows` disabled the same interleaving is not blocked and removes both owners.

    Proves the race exists on PostgreSQL (READ COMMITTED) and that the two tests above would notice a missing lock.
    """
    async def no_lock(db: AsyncSession, org_id: str) -> None:
        return None

    monkeypatch.setattr(service, "_lock_owner_rows", no_lock)

    async def go():
        org, a, b = await _seed_two_owners(migrated)
        r1, r2 = await _interleave(
            migrated,
            lambda s1: service.delete_member_guarded(s1, org, a),
            lambda s2: service.delete_member_guarded(s2, org, b),
            expect_block=False)
        return r1, r2, await _roles(migrated, org)

    r1, r2, roles = asyncio.run(go())
    assert (r1, r2) == (True, True)
    assert _owners(roles) == []


# ---------------------------------------------------------------- usage counter upsert (SAVEPOINT path)
def test_concurrent_first_usage_writes_are_summed(migrated: Settings, monkeypatch: pytest.MonkeyPatch):
    # Both sessions must target the same period even if the month changes mid-test.
    monkeypatch.setattr(service, "current_period", lambda now=None: "2026-10")

    async def go():
        org, _, _ = await _seed_two_owners(migrated)
        # s1 inserts the first row and holds it; s2 hits the unique key, waits, then retries as UPDATE after the
        # IntegrityError rolled back its SAVEPOINT.
        await _interleave(
            migrated,
            lambda s1: service.add_usage(s1, org, runs=1, upload_bytes=10),
            lambda s2: service.add_usage(s2, org, runs=2, upload_bytes=20),
            expect_block=True)
        engine = create_engine(migrated)
        try:
            async with make_sessionmaker(engine)() as db:
                rows = (await db.execute(select(UsageCounter).where(UsageCounter.org_id == org))).scalars().all()
                total = (await db.execute(select(func.count()).select_from(UsageCounter))).scalar_one()
                return [(r.period, r.runs, r.upload_bytes) for r in rows], total
        finally:
            await engine.dispose()

    rows, total = asyncio.run(go())
    assert rows == [("2026-10", 3, 30)]  # the frozen period proves the patch reached add_usage
    assert total == 1


# ---------------------------------------------------------------- OAuth grant state under READ COMMITTED
_OAUTH_SESSION: contextvars.ContextVar[AsyncSession | None] = contextvars.ContextVar(
    "test_oauth_pg_session", default=None)


@dataclass
class OAuthConnection:
    client: OAuthClientInformationFull
    grant_id: str
    pair: TokenResponse
    refresh: oauth.DaasRefreshToken


@asynccontextmanager
async def _oauth_runtime(settings: Settings) -> AsyncIterator[tuple[oauth.DaasOAuthProvider, AsyncClient]]:
    """Real SDK routes and MCP authentication; no mailer or application background tasks are needed.

    The terminal MCP handler is inert: assertions about 401 exercise the production gateway and provider.
    """
    settings = settings.model_copy(update={
        "daas_rate_limit_ip_per_min": 0, "daas_rate_limit_mcp_per_min": 0,
        "daas_rate_limit_auth_fail_per_min": 0,
    })
    engine = create_engine(settings)
    provider = oauth.DaasOAuthProvider(settings, make_sessionmaker(engine), issuer=ISSUER)
    original_session = provider._session

    @asynccontextmanager
    async def session() -> AsyncIterator[AsyncSession]:
        bound = _OAUTH_SESSION.get()
        if bound is not None:
            yield bound  # keep the harness's connection pinned across loader/exchange commits
        else:
            async with original_session() as db:
                yield db

    provider._session = session
    routes = oauth.build_routes(provider, AnyHttpUrl(ISSUER))
    routes.append(Route("/mcp", endpoint=McpGateway(JSONResponse({"ok": True}), max_body_bytes=4096),
                        methods=["POST"]))
    app = Starlette(routes=routes)
    app.state.settings = settings
    app.state.limiter = RateLimiter()
    app.state.oauth = provider
    app.state.db_sessionmaker = make_sessionmaker(engine)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url=ISSUER) as client:
            yield provider, client
    finally:
        await engine.dispose()


async def _seed_oauth_connection(provider: oauth.DaasOAuthProvider) -> OAuthConnection:
    """Synthetic approved consent; registration and first exchange use the production provider."""
    client = OAuthClientInformationFull(
        client_id=f"TEST-{uuid.uuid4().hex}", client_name="TestBrand", redirect_uris=[AnyUrl(REDIRECT)],
        token_endpoint_auth_method="none", scope="runs:read")
    await provider.register_client(client)
    _, challenge = pkce_pair()
    raw_code = f"TEST-code-{uuid.uuid4().hex}"
    now = utcnow()
    async with provider._session() as db:
        user, _ = await service.get_or_create_user(db, f"test-{uuid.uuid4().hex}@example.test")
        org = await service.create_org(db, name="TestBrand", owner=user)
        code = models.OAuthCode(
            client_id=client.client_id, org_id=org.id, user_id=user.id, code_hash=sha256_hex(raw_code),
            scopes=["runs:read"], code_challenge=challenge, redirect_uri=REDIRECT,
            redirect_uri_provided_explicitly=True, resource=RESOURCE, status="approved",
            created_at=now, expires_at=now + oauth.CODE_TTL)
        db.add(code)
        await db.commit()
    loaded = await provider.load_authorization_code(client, raw_code)
    assert loaded is not None
    pair = await provider.exchange_authorization_code(client, loaded)
    refresh = await provider.load_refresh_token(client, pair.refresh_token)
    assert refresh is not None
    async with provider._session() as db:
        grant_id = (await db.execute(select(models.OAuthToken.grant_id).where(
            models.OAuthToken.token_hash == sha256_hex(pair.refresh_token)))).scalar_one()
    assert grant_id is not None
    return OAuthConnection(client, grant_id, pair, refresh)


@asynccontextmanager
async def _oauth_pair(settings: Settings) -> AsyncIterator[Pair]:
    """Provider methods commit internally; pinned connections keep pg_backend_pid stable throughout a request."""
    engine = create_engine(settings)
    monitor_engine = create_async_engine(resolve_db_url(settings), isolation_level="AUTOCOMMIT")
    try:
        async with (engine.connect() as c1, engine.connect() as c2, monitor_engine.connect() as monitor,
                    AsyncSession(bind=c1, expire_on_commit=False) as s1,
                    AsyncSession(bind=c2, expire_on_commit=False) as s2):
            yield Pair(make_sessionmaker(engine), s1, s2, await _backend_pid(s1), await _backend_pid(s2), monitor)
    finally:
        await monitor_engine.dispose()
        await engine.dispose()


async def _oauth_interleave(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    first: Callable[[], Awaitable[Any]],
    second: Callable[[], Awaitable[Any]],
    *,
    expect_block: bool,
    disable_refresh_guard: bool = False,
) -> tuple[Any, Any]:
    """Pause the first production operation immediately after its guard, before token/grant writes.

    Unlike the owner-row harness, both operations run as tasks and own their commits. The only injected fault
    is the negative control's missing guard in session 2; loaders, CAS, issuance and revocation remain real.
    """
    async with _oauth_pair(settings) as pair:
        entered, release = asyncio.Event(), asyncio.Event()

        async def after_guard() -> None:
            if _OAUTH_SESSION.get() is pair.s1 and not entered.is_set():
                entered.set()
                await release.wait()

        original_guard = oauth._guard_grant

        async def selective_guard(db: AsyncSession, grant_id: str) -> bool:
            if disable_refresh_guard and _OAUTH_SESSION.get() is pair.s2:
                return True
            return await original_guard(db, grant_id)

        async def bound(db: AsyncSession, operation: Callable[[], Awaitable[Any]]) -> Any:
            marker = _OAUTH_SESSION.set(db)
            try:
                return await operation()
            finally:
                _OAUTH_SESSION.reset(marker)

        with monkeypatch.context() as patch:
            patch.setattr(oauth, "_TEST_AFTER_GUARD", after_guard)
            patch.setattr(oauth, "_guard_grant", selective_guard)
            task1 = asyncio.create_task(bound(pair.s1, first))
            task2: asyncio.Task | None = None
            try:
                await asyncio.wait_for(entered.wait(), _BLOCK_TIMEOUT_S)
                task2 = asyncio.create_task(bound(pair.s2, second))
                if expect_block:
                    await _wait_until_blocked(pair, task2)
                    release.set()
                    result1 = await asyncio.wait_for(task1, _BLOCK_TIMEOUT_S)
                    result2 = await asyncio.wait_for(task2, _BLOCK_TIMEOUT_S)
                else:
                    # Completion while s1 still owns the grant proves that s2 did not wait on its lock.
                    result2 = await asyncio.wait_for(asyncio.shield(task2), _NO_BLOCK_TIMEOUT_S)
                    blocked = (await pair.monitor.execute(
                        text("SELECT :blocker = ANY(pg_blocking_pids(:waiter))"),
                        {"blocker": pair.pid1, "waiter": pair.pid2})).scalar_one()
                    assert not blocked
                    assert not task1.done()
                    release.set()
                    result1 = await asyncio.wait_for(task1, _BLOCK_TIMEOUT_S)
                return result1, result2
            finally:
                release.set()
                for task in (task1, task2):
                    if task is not None:
                        if not task.done():
                            task.cancel()
                        with contextlib.suppress(BaseException):
                            await task


async def _oauth_refresh(client: AsyncClient, connection: OAuthConnection, raw: str | None = None) -> Response:
    return await client.post("/token", data={
        "grant_type": "refresh_token", "client_id": connection.client.client_id,
        "refresh_token": raw or connection.pair.refresh_token,
    })


async def _oauth_revoke(client: AsyncClient, connection: OAuthConnection) -> Response:
    return await client.post("/revoke", data={
        "client_id": connection.client.client_id, "token": connection.pair.refresh_token,
        "token_type_hint": "refresh_token",
    })


async def _assert_oauth_grant_closed(
    provider: oauth.DaasOAuthProvider,
    client: AsyncClient,
    connection: OAuthConnection,
    *,
    token_count: int,
    access: str | None = None,
    reason: str = "client_revoked",
) -> None:
    async with provider._session() as db:
        grant = await db.get(models.OAuthGrant, connection.grant_id)
        rows = (await db.execute(select(models.OAuthToken).where(
            models.OAuthToken.grant_id == connection.grant_id))).scalars().all()
        events = (await db.execute(select(models.AuditEvent).where(
            models.AuditEvent.action == "oauth.token_revoked",
            models.AuditEvent.target == f"oauth:{connection.client.client_id}"))).scalars().all()
    assert grant.revoked_at is not None and grant.revocation_reason == reason
    assert len(rows) == token_count
    assert all(row.revoked_at is not None for row in rows)
    assert len(events) == 1 and events[0].meta["reason"] == reason
    # Both the original access token and any just-issued pair must fail the real MCP gateway.
    for raw in (connection.pair.access_token, access):
        if raw is not None:
            response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                         headers={"Authorization": f"Bearer {raw}"})
            assert response.status_code == 401, response.text
    denied = await _oauth_refresh(client, connection)
    assert denied.status_code == 400 and denied.json()["error"] == "invalid_grant"


def test_pg_revoke_waits_for_refresh_and_catches_the_new_pair(migrated: Settings, monkeypatch: pytest.MonkeyPatch):
    async def go():
        async with _oauth_runtime(migrated) as (provider, client):
            connection = await _seed_oauth_connection(provider)
            rotated, revoked = await _oauth_interleave(
                migrated, monkeypatch,
                lambda: provider.exchange_refresh_token(connection.client, connection.refresh, ["runs:read"]),
                lambda: _oauth_revoke(client, connection), expect_block=True)
            assert revoked.status_code == 200
            await _assert_oauth_grant_closed(provider, client, connection, token_count=4,
                                             access=rotated.access_token)
            denied = await _oauth_refresh(client, connection, rotated.refresh_token)
            assert denied.status_code == 400 and denied.json()["error"] == "invalid_grant"

    asyncio.run(go())


def test_pg_refresh_waits_for_revoke_and_issues_nothing(migrated: Settings, monkeypatch: pytest.MonkeyPatch):
    async def go():
        async with _oauth_runtime(migrated) as (provider, client):
            connection = await _seed_oauth_connection(provider)
            revoked, refreshed = await _oauth_interleave(
                migrated, monkeypatch, lambda: _oauth_revoke(client, connection),
                lambda: _oauth_refresh(client, connection), expect_block=True)
            assert revoked.status_code == 200
            assert refreshed.status_code == 400 and refreshed.json()["error"] == "invalid_grant"
            await _assert_oauth_grant_closed(provider, client, connection, token_count=2)

    asyncio.run(go())


def test_pg_reuse_waits_for_the_current_rotation(migrated: Settings, monkeypatch: pytest.MonkeyPatch):
    async def go():
        async with _oauth_runtime(migrated) as (provider, client):
            connection = await _seed_oauth_connection(provider)
            pair1 = await provider.exchange_refresh_token(connection.client, connection.refresh, ["runs:read"])
            refresh1 = await provider.load_refresh_token(connection.client, pair1.refresh_token)
            assert refresh1 is not None
            pair2, reused = await _oauth_interleave(
                migrated, monkeypatch,
                lambda: provider.exchange_refresh_token(connection.client, refresh1, ["runs:read"]),
                lambda: _oauth_refresh(client, connection), expect_block=True)
            assert reused.status_code == 400 and reused.json()["error"] == "invalid_grant"
            await _assert_oauth_grant_closed(provider, client, connection, token_count=6,
                                             access=pair2.access_token, reason="reuse_detected")
            denied = await _oauth_refresh(client, connection, pair2.refresh_token)
            assert denied.status_code == 400 and denied.json()["error"] == "invalid_grant"

    asyncio.run(go())


def test_pg_other_grant_is_not_blocked(migrated: Settings, monkeypatch: pytest.MonkeyPatch):
    async def go():
        async with _oauth_runtime(migrated) as (provider, client):
            first = await _seed_oauth_connection(provider)
            other = await _seed_oauth_connection(provider)
            assert first.grant_id != other.grant_id
            revoked, refreshed = await _oauth_interleave(
                migrated, monkeypatch, lambda: _oauth_revoke(client, first),
                lambda: _oauth_refresh(client, other), expect_block=False)
            assert revoked.status_code == 200
            assert refreshed.status_code == 200, refreshed.text
            assert await provider.principal_for(refreshed.json()["access_token"]) is not None
            await _assert_oauth_grant_closed(provider, client, first, token_count=2)
            async with provider._session() as db:
                grant = await db.get(models.OAuthGrant, other.grant_id)
            assert grant.revoked_at is None

    asyncio.run(go())


@pytest.mark.parametrize("guard_enabled", [True, False], ids=["shared-guard", "missing-refresh-guard"])
def test_pg_refresh_racing_a_revoke_is_refused_not_answered_with_a_dead_pair(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch, guard_enabled: bool,
):
    """D13 negative control: a missing guard returns 200 before revocation commits, producing a dead pair."""
    async def go():
        async with _oauth_runtime(migrated) as (provider, client):
            connection = await _seed_oauth_connection(provider)
            revoked, refreshed = await _oauth_interleave(
                migrated, monkeypatch, lambda: _oauth_revoke(client, connection),
                lambda: _oauth_refresh(client, connection), expect_block=guard_enabled,
                disable_refresh_guard=not guard_enabled)
            assert revoked.status_code == 200
            if guard_enabled:
                assert refreshed.status_code == 400 and refreshed.json()["error"] == "invalid_grant"
                await _assert_oauth_grant_closed(provider, client, connection, token_count=2)
            else:
                # The HTTP response already succeeded while revoke was paused before touching any tokens.
                assert refreshed.status_code == 200, refreshed.text
                dead = refreshed.json()
                await _assert_oauth_grant_closed(provider, client, connection, token_count=4,
                                                 access=dead["access_token"])
                denied = await _oauth_refresh(client, connection, dead["refresh_token"])
                assert denied.status_code == 400 and denied.json()["error"] == "invalid_grant"

    asyncio.run(go())

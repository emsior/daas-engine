"""Operacje domenowe control plane uzywane przez API v1 i CLI (bez zaleznosci od FastAPI)."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.control import scopes as sc
from app.control.models import ApiKey, Invitation, Membership, Org, UsageCounter, User
from app.control.security import new_api_key
from app.control.types import utcnow

DEFAULT_ORG_ID = "default"
DEFAULT_ORG_NAME = "Default"

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s.]{2,}$")


def normalize_email(email: str) -> str:
    """Male litery, bez bialych znakow. Jedyna forma przechowywana w bazie."""
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email)) and len(email) <= 254


def slugify(name: str) -> str:
    """ASCII, male litery, myslniki. Polskie znaki sa transliterowane przez dekompozycje (ł osobno)."""
    text = name.replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "org"


async def unique_slug(db: AsyncSession, name: str) -> str:
    base = slugify(name)
    candidate, n = base, 1
    while (await db.execute(select(Org.id).where(Org.slug == candidate))).first() is not None:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


async def get_or_create_user(db: AsyncSession, email: str, *, locale: str = "pl") -> tuple[User, bool]:
    email = normalize_email(email)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is not None:
        return user, False
    user = User(email=email, locale=locale)
    db.add(user)
    await db.flush()
    return user, True


async def create_org(
    db: AsyncSession,
    *,
    name: str,
    owner: User | None,
    plan: str = "free",
    locale: str = "pl",
    retention_days: int = 90,
    dpa_version: str | None = None,
    org_id: str | None = None,
) -> Org:
    """Tworzy organizacje i (gdy podano) czlonkostwo wlasciciela. Nie robi commitu."""
    org = Org(
        name=name.strip(), slug=await unique_slug(db, name), plan=plan, locale=locale,
        retention_days=retention_days, dpa_version=dpa_version,
        dpa_accepted_at=utcnow() if dpa_version else None,
    )
    if org_id:
        org.id = org_id
    db.add(org)
    await db.flush()
    if owner is not None:
        db.add(Membership(org_id=org.id, user_id=owner.id, role="owner"))
        await db.flush()
    return org


async def issue_api_key(
    db: AsyncSession,
    *,
    org_id: str,
    name: str,
    scopes: list[str],
    created_by: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[ApiKey, str]:
    """Tworzy klucz. Zwraca `(rekord, klucz_jawny)`; jawny klucz istnieje tylko w tej odpowiedzi."""
    for _ in range(5):
        raw, prefix, key_hash = new_api_key()
        taken = (await db.execute(select(ApiKey.id).where(ApiKey.prefix == prefix))).first()
        if taken is None:
            break
    else:  # pragma: no cover - 5 kolizji 32-bitowego prefiksu z rzedu
        raise RuntimeError("could not allocate a unique API key prefix")
    unknown = sorted(set(scopes) - set(sc.KEY_SCOPES))
    if unknown:
        raise ValueError(f"unknown scopes: {unknown}")
    key = ApiKey(org_id=org_id, name=name.strip(), prefix=prefix, key_hash=key_hash,
                 scopes=sorted(set(scopes)), created_by=created_by, expires_at=expires_at)
    db.add(key)
    await db.flush()
    return key, raw


def expiry_in_days(days: int | None) -> datetime | None:
    return utcnow() + timedelta(days=days) if days else None


async def ensure_default_org(db: AsyncSession, *, retention_days: int = 90) -> Org:
    """Organizacja `default` trybu selfhost (idempotentnie). Nie robi commitu."""
    org = await db.get(Org, DEFAULT_ORG_ID)
    if org is not None:
        return org
    return await create_org(
        db, name=DEFAULT_ORG_NAME, owner=None, plan="selfhost", retention_days=retention_days,
        org_id=DEFAULT_ORG_ID,
    )


async def memberships_of(db: AsyncSession, user_id: str) -> list[tuple[Membership, Org]]:
    """Czlonkostwa uzytkownika wraz z organizacjami (najstarsze pierwsze)."""
    rows = await db.execute(
        select(Membership, Org).join(Org, Org.id == Membership.org_id)
        .where(Membership.user_id == user_id).order_by(Membership.created_at, Org.id))
    return [(m, o) for m, o in rows.all()]


async def accept_pending_invitations(db: AsyncSession, user: User) -> list[tuple[str, str]]:
    """Zamienia oczekujace zaproszenia na adres uzytkownika w czlonkostwa. Nie robi commitu.

    Wolane po udowodnieniu wlasnosci adresu (magic link) i przy `GET /me` sesji. Wygasle zaproszenia sa pomijane.
    Zwraca liste `(org_id, role)` NOWO utworzonych czlonkostw (puste, gdy nic nie dodano) — wolajacy zapisuje
    na jej podstawie zdarzenie audytu `member.added` (sam adres jest juz znany jako `user.email`).
    """
    now = utcnow()
    pending = (await db.execute(select(Invitation).where(
        Invitation.email == user.email, Invitation.accepted_at.is_(None), Invitation.expires_at > now,
    ))).scalars().all()
    added: list[tuple[str, str]] = []
    for invitation in pending:
        exists = await db.get(Membership, (invitation.org_id, user.id))
        if exists is None:
            db.add(Membership(org_id=invitation.org_id, user_id=user.id, role=invitation.role))
            added.append((invitation.org_id, invitation.role))
        invitation.accepted_at = now
    if pending:
        await db.flush()
    return added


def current_period(now: datetime | None = None) -> str:
    return (now or utcnow()).strftime("%Y-%m")


async def add_usage(db: AsyncSession, org_id: str, *, runs: int = 0, upload_bytes: int = 0) -> None:
    """Zwieksza licznik uzycia biezacego miesiaca (upsert odporny na wyscig dwoch pierwszych zapisow)."""
    period = current_period()

    async def bump() -> int:
        result = await db.execute(update(UsageCounter).where(
            UsageCounter.org_id == org_id, UsageCounter.period == period,
        ).values(runs=UsageCounter.runs + runs, upload_bytes=UsageCounter.upload_bytes + upload_bytes))
        return result.rowcount

    if await bump():
        return
    try:
        async with db.begin_nested():
            db.add(UsageCounter(org_id=org_id, period=period, runs=runs, upload_bytes=upload_bytes))
            await db.flush()
    except IntegrityError:
        await bump()


# ----------------------------------------------------------------------------- czlonkowie: niezmiennik "min. 1 owner"
def _other_owners_exist(org_id: str):
    """Podzapytanie skalarne: czy w organizacji sa co najmniej DWIE role `owner` (alias, zeby nie korelowalo z celem)."""
    m = aliased(Membership)
    return select(func.count()).select_from(m).where(m.org_id == org_id, m.role == "owner").scalar_subquery() > 1


async def _lock_owner_rows(db: AsyncSession, org_id: str) -> None:
    """Postgres: blokuje wiersze ownerow, zeby rownolegle zmiany ownerow w tej organizacji szly po kolei
    (READ COMMITTED widzialby inaczej ten sam stary stan). SQLite ignoruje FOR UPDATE (zapisy i tak sa szeregowane)."""
    await db.execute(select(Membership.user_id).where(
        Membership.org_id == org_id, Membership.role == "owner").with_for_update())


async def delete_member_guarded(db: AsyncSession, org_id: str, user_id: str) -> bool:
    """Usuwa czlonkostwo jednym warunkowym DELETE: owner znika tylko, gdy zostaje inny owner (atomowo, bez
    osobnego liczenia). `False` = nic nie usunieto (brak czlonka albo to ostatni owner); rozroznia wolajacy."""
    await _lock_owner_rows(db, org_id)
    result = await db.execute(delete(Membership).where(
        Membership.org_id == org_id, Membership.user_id == user_id,
        or_(Membership.role != "owner", _other_owners_exist(org_id))).execution_options(synchronize_session=False))
    return result.rowcount == 1


async def set_member_role_guarded(db: AsyncSession, org_id: str, user_id: str, role: str) -> bool:
    """Zmienia role jednym warunkowym UPDATE; degradacja ownera tylko, gdy zostaje inny owner. `False` jak wyzej."""
    await _lock_owner_rows(db, org_id)
    result = await db.execute(update(Membership).where(
        Membership.org_id == org_id, Membership.user_id == user_id,
        or_(Membership.role != "owner", _other_owners_exist(org_id))).values(role=role).execution_options(
        synchronize_session=False))
    return result.rowcount == 1

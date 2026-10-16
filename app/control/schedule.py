"""Harmonogram organizacji: cotygodniowe przypomnienie o eksporcie i petla, ktora je wysyla (`SchedulerLoop`).

Kontrakt (SPEC harmonogramu, 12.10.2026):
    * `compute_next_reminder` liczy najblizszy termin `weekday` `hour:00` w strefie IANA, zwraca UTC; zmiany czasu
      obsluguje `zoneinfo` (godzina nieistniejaca = pierwsza chwila po przeskoku, zdublowana = pierwsze wystapienie),
    * `send_reminder` najpierw ZAJMUJE termin warunkowym UPDATE (`next_reminder_at` przesuniete o tydzien,
      `last_reminder_at = now`), dopiero potem wysyla — dwa ticki albo restart w trakcie nie daja dwoch maili
      (co najwyzej jeden); wiersz, dla ktorego nie da sie policzyc nastepnego terminu (strefa nieznana po zmianie
      `tzdata`), jest wylaczany z audytem `skipped_reason=unknown_timezone` zamiast wracac w kazdym ticku,
    * adresaci = czlonkowie organizacji (owner/admin/member); tresc bez tokenow i bez danych klienta,
    * `SchedulerLoop` dziala wylacznie w trybie cloud (lifespan), jeden proces (`workers=1`) jak reszta 2.0.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.control.audit import record_event
from app.control.mailer import Mailer, reminder_mail
from app.control.models import Membership, Org, OrgSchedule, Upload, User
from app.control.types import utcnow
from app.core.config import Settings

log = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/Warsaw"
#: Przypomnienie jest pomijane, gdy organizacja wgrala plik w ciagu tylu dni (`reminder_skip_if_recent_upload`).
RECENT_UPLOAD_WINDOW = timedelta(days=6)
#: Najmniejszy dopuszczalny odstep petli (sekundy); `DAAS_SCHEDULER_TICK_SEC` ponizej jest odrzucany przez Settings.
MIN_TICK_SEC = 5

#: Wartosci domyslne harmonogramu (organizacja bez wiersza w `org_schedules`). `notify_webhook_url` (webhook czatu)
#: zawiera sekret: API go nie zwraca (`webhook_hint`), a `PUT` bez tego pola zostawia zapisany adres.
DEFAULTS: dict[str, object] = {
    "reminder_enabled": False, "reminder_weekday": 0, "reminder_hour": 8, "timezone": DEFAULT_TIMEZONE,
    "reminder_skip_if_recent_upload": True, "auto_run_on_upload": False, "notify_on_finish": False,
    "notify_webhook_url": None, "notify_webhook_kind": "slack",
}
EDITABLE_FIELDS: tuple[str, ...] = tuple(DEFAULTS)
#: Koncowke sciezki pokazujemy tylko przy sciezce co najmniej tej dlugosci (krotka moze byc cala sekretem).
_HINT_MIN_PATH = 16
_HINT_TAIL = 4


def webhook_hint(url: str | None) -> str | None:
    """Podpis zapisanego adresu webhooka czatu bez sekretu: `<host>/...<4 ostatnie znaki sciezki>` (wielokropek
    U+2026), np. `hooks.slack.com/...XyZ9`. Bez zapytania (Teams ma w nim podpis `sig`), bez portu i danych logowania;
    krotka sciezka nie jest ujawniana wcale (sam host). None, gdy adresu brak."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        host, parts = "", None
    if not host or parts is None:
        return "\u2026"
    path = parts.path.rstrip("/")
    return f"{host}/\u2026{path[-_HINT_TAIL:]}" if len(path) >= _HINT_MIN_PATH else host


@lru_cache(maxsize=1)
def known_timezones() -> frozenset[str]:
    """Strefy IANA znane interpreterowi (`tzdata` z locka na Windows)."""
    return frozenset(available_timezones())


def is_valid_timezone(name: str) -> bool:
    return name in known_timezones()


def compute_next_reminder(weekday: int, hour: int, tz: str | ZoneInfo, after: datetime) -> datetime:
    """Najblizszy termin `weekday` (0 = poniedzialek) o `hour:00` czasu lokalnego w `tz`, scisle pozniejszy niz
    `after`; wynik w UTC (ze strefa). Naiwne `after` jest traktowane jako UTC.

    Dzien zmiany czasu: godzina, ktorej nie ma (np. 02:00 w Europe/Warsaw 29.03.2026), wypada w pierwszej chwili
    po przeskoku (03:00 czasu letniego); godzina zdublowana (02:00 25.10.2026) to jej pierwsze wystapienie (`fold=0`).
    """
    if not 0 <= weekday <= 6:
        raise ValueError("weekday must be 0..6")
    if not 0 <= hour <= 23:
        raise ValueError("hour must be 0..23")
    zone = tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    start: date = after.astimezone(zone).date()
    for offset in range(15):  # dwa pelne tygodnie: zawsze wystarcza (termin w tym tygodniu mogl juz minac)
        day = start + timedelta(days=offset)
        if day.weekday() != weekday:
            continue
        local = datetime(day.year, day.month, day.day, hour, 0, tzinfo=zone, fold=0)
        candidate = local.astimezone(UTC)
        if candidate > after:
            return candidate
    raise AssertionError("unreachable: a weekday repeats every 7 days")  # pragma: no cover


def next_for(schedule: OrgSchedule, after: datetime) -> datetime | None:
    """`next_reminder_at` dla stanu harmonogramu: None, gdy przypomnienie jest wylaczone."""
    if not schedule.reminder_enabled:
        return None
    return compute_next_reminder(schedule.reminder_weekday, schedule.reminder_hour, schedule.timezone, after)


def default_schedule(org_id: str) -> OrgSchedule:
    """Obiekt z wartosciami domyslnymi (nie jest dodawany do sesji)."""
    return OrgSchedule(org_id=org_id, next_reminder_at=None, last_reminder_at=None, updated_at=None, **DEFAULTS)


async def get_schedule(db: AsyncSession, org_id: str) -> OrgSchedule | None:
    return await db.get(OrgSchedule, org_id)


async def due_schedules(db: AsyncSession, now: datetime) -> list[str]:
    """Organizacje, ktorym nalezy sie przypomnienie: `reminder_enabled AND next_reminder_at <= now`."""
    rows = await db.execute(select(OrgSchedule.org_id).where(
        OrgSchedule.reminder_enabled.is_(True), OrgSchedule.next_reminder_at.is_not(None),
        OrgSchedule.next_reminder_at <= now).order_by(OrgSchedule.next_reminder_at, OrgSchedule.org_id))
    return list(rows.scalars().all())


async def org_recipients(db: AsyncSession, org_id: str) -> list[tuple[str, str]]:
    """Czlonkowie organizacji z adresem e-mail: `(email, locale)`, posortowane po adresie."""
    rows = await db.execute(select(User.email, User.locale).join(Membership, Membership.user_id == User.id).where(
        Membership.org_id == org_id, Membership.role.in_(("owner", "admin", "member"))).order_by(User.email))
    return [(email, locale or "pl") for email, locale in rows.all() if email]


async def send_mails(mailer: Mailer, mails) -> int:
    """Wysyla wiadomosci po kolei (blokujaco, w watku). Blad jednego adresata nie zatrzymuje pozostalych.
    Zwraca liczbe wyslanych."""
    sent = 0
    for mail in mails:
        try:
            await run_in_threadpool(mailer.send, mail)
            sent += 1
        except Exception:  # noqa: BLE001
            log.exception("scheduled e-mail failed")
    return sent


@dataclass(frozen=True)
class ReminderResult:
    org_id: str
    sent: bool
    recipients: int
    skipped_reason: str | None = None


async def send_reminder(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, mailer: Mailer,
                        org_id: str, *, now: datetime | None = None) -> ReminderResult | None:
    """Przypomnienie jednej organizacji. None = termin juz zajety (inny tick, restart) albo nieaktualny."""
    now = now or utcnow()
    async with sessionmaker() as db:
        schedule = await db.get(OrgSchedule, org_id)
        if (schedule is None or not schedule.reminder_enabled or schedule.next_reminder_at is None
                or schedule.next_reminder_at > now):
            return None
        due_at = schedule.next_reminder_at
        try:
            following: datetime | None = compute_next_reminder(
                schedule.reminder_weekday, schedule.reminder_hour, schedule.timezone, now)
            disabled = False
        except (ZoneInfoNotFoundError, ValueError) as exc:
            # Wiersz, ktorego nie da sie policzyc (strefa zniknela z bazy tzdata albo dane spoza walidacji PUT):
            # bez tego przypomnienie wracaloby w kazdym ticku z wyjatkiem w logu, bez konca. Wylaczamy je
            # (widoczne w kokpicie i w audycie); wlasciciel wlacza ponownie z poprawna strefa.
            log.warning("reminder disabled org_id=%s: cannot compute the next slot (%s)", org_id, type(exc).__name__)
            following, disabled = None, True
        claimed = await db.execute(update(OrgSchedule).where(
            OrgSchedule.org_id == org_id, OrgSchedule.reminder_enabled.is_(True),
            OrgSchedule.next_reminder_at == due_at,
        ).values(next_reminder_at=following, last_reminder_at=now, reminder_enabled=not disabled)
         .execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            await db.rollback()
            return None
        await db.commit()

        skip = (not disabled and schedule.reminder_skip_if_recent_upload and (await db.execute(select(exists().where(
            Upload.org_id == org_id, Upload.created_at >= now - RECENT_UPLOAD_WINDOW)))).scalar())
        org = await db.get(Org, org_id)
        recipients = [] if disabled or skip or org is None else await org_recipients(db, org_id)

    if disabled:
        result = ReminderResult(org_id, sent=False, recipients=0, skipped_reason="unknown_timezone")
    elif skip:
        result = ReminderResult(org_id, sent=False, recipients=0, skipped_reason="recent_upload")
    elif not recipients:
        result = ReminderResult(org_id, sent=False, recipients=0, skipped_reason="no_recipients")
    else:
        sent = await send_mails(mailer, [reminder_mail(settings, email, org.name, locale)
                                         for email, locale in recipients])
        result = ReminderResult(org_id, sent=sent > 0, recipients=sent,
                                skipped_reason=None if sent else "mail_failed")
    async with sessionmaker() as db:
        await record_event(db, None, org_id=org_id, actor_type="system", actor_id=None, action="schedule.reminder",
                           target=f"org:{org_id}", settings=settings,
                           meta={"sent": result.sent, "recipients": result.recipients,
                                 **({"skipped_reason": result.skipped_reason} if result.skipped_reason else {})})
        await db.commit()
    return result


class SchedulerLoop:
    """Petla przypomnien w lifespanie: co `tick_sec` sekund `due_schedules` -> `send_reminder` per organizacja.

    Pierwszy przebieg od razu po starcie (zalegle przypomnienie po restarcie wychodzi raz, nastepne liczone od
    biezacej chwili). Blad jednej organizacji jest logowany i nie zatrzymuje pozostalych ani petli."""

    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], mailer: Mailer, *,
                 tick_sec: float | None = None) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.mailer = mailer
        self.tick_sec = max(float(MIN_TICK_SEC), float(tick_sec or settings.daas_scheduler_tick_sec))
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.get_running_loop().create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        """Bezpieczne zatrzymanie (shutdown): anuluje petle i czeka na jej koniec."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def tick(self, now: datetime | None = None) -> list[ReminderResult]:
        """Jedna iteracja (uzywana przez petle i testy)."""
        now = now or utcnow()
        async with self.sessionmaker() as db:
            due = await due_schedules(db, now)
        results: list[ReminderResult] = []
        for org_id in due:
            try:
                result = await send_reminder(self.sessionmaker, self.settings, self.mailer, org_id, now=now)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("scheduled reminder failed org_id=%s", org_id)
                continue
            if result is not None:
                results.append(result)
        return results

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.tick_sec)


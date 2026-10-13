"""Schematy zadan i odpowiedzi API v1 (Pydantic). Zrodlo typow dla `docs/openapi.json` i kokpitu."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.control import scopes as sc
from app.control.service import is_valid_email, normalize_email
from app.core.models import PERIOD_MAX_DAYS, check_period_date, check_period_range

Locale = Literal["pl", "en"]
RoleName = Literal["owner", "admin", "member"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
PipelineId = Literal["cs2_demo", "ecommerce_demo", "uksc_evidence"]
UploadKind = Literal["csv", "xlsx", "package"]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: Zakres dat runu (`JobCreate.period_from/period_to`): najwczesniejsza dozwolona data i najdluzszy zakres w dniach
#: (oba konce wlacznie). Starsze daty i dluzsze zakresy to niemal zawsze pomylka w formacie albo w roku.


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _email(value: str) -> str:
    value = normalize_email(value)
    if not is_valid_email(value):
        raise ValueError("invalid e-mail address")
    return value


# ----------------------------------------------------------------------------- auth, /me
class MagicLinkRequest(BaseModel):
    email: str = Field(..., examples=["jan@example.test"], max_length=254)
    locale: Locale = "pl"
    _norm = field_validator("email")(_email)


class MagicLinkAccepted(BaseModel):
    status: Literal["sent"] = "sent"
    expires_in_minutes: int = Field(..., examples=[15])


class MagicLinkVerify(BaseModel):
    token: str = Field(..., min_length=20, max_length=200, examples=["Zm9v...token-z-maila"])


class UserOut(Out):
    id: str
    email: str
    name: str | None = None
    locale: Locale = "pl"


class MembershipOut(BaseModel):
    org_id: str
    org_name: str
    org_slug: str
    role: RoleName | None = Field(default=None, description="rola uzytkownika; brak dla klucza API")


class PrincipalOut(BaseModel):
    type: Literal["user", "api_key"]
    org_id: str | None = Field(default=None, description="dla kluczy API: organizacja klucza")
    scopes: list[str] = Field(default_factory=list, description="dla kluczy API; dla uzytkownika wynikaja z roli")


class MeOut(BaseModel):
    principal: PrincipalOut
    user: UserOut | None = None
    orgs: list[MembershipOut]


class SessionOut(BaseModel):
    user: UserOut
    orgs: list[MembershipOut]
    csrf_token: str = Field(..., description="to samo co cookie daas_csrf; odeslij w naglowku X-CSRF-Token")
    expires_at: datetime


# ----------------------------------------------------------------------------- organizacje
class OrgOut(Out):
    id: str
    slug: str
    name: str
    plan: Literal["free", "pro", "selfhost"]
    locale: Locale
    retention_days: int
    llm_narrative: bool
    dpa_version: str | None = None
    dpa_accepted_at: datetime | None = None
    created_at: datetime


class OrgCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, examples=["Sklep Przykładowy sp. z o.o."])
    dpa_accepted: bool = Field(..., description="musi byc true: akceptacja umowy powierzenia (DPA)")
    dpa_version: Literal["v1"] = "v1"
    locale: Locale = "pl"

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    llm_narrative: bool | None = Field(default=None, description="opt-in: streszczenie raportu przez zewnetrzny LLM")
    locale: Locale | None = None


# ----------------------------------------------------------------------------- czlonkowie
class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str | None = None
    role: RoleName
    created_at: datetime


class MemberCreate(BaseModel):
    email: str = Field(..., max_length=254, examples=["ola@example.test"])
    role: RoleName = "member"
    _norm = field_validator("email")(_email)


class InvitationCreate(MemberCreate):
    pass


class InvitationOut(BaseModel):
    id: str
    email: str
    role: RoleName
    created_at: datetime
    expires_at: datetime


# ----------------------------------------------------------------------------- klucze API
class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120, examples=["n8n produkcja"])
    scopes: list[str] = Field(..., min_length=1, examples=[["uploads:write", "runs:write", "reports:read"]])
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("scopes")
    @classmethod
    def _known(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(sc.KEY_SCOPES))
        if unknown:
            raise ValueError(f"unknown scopes: {', '.join(unknown)}")
        return sorted(set(value))

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str = Field(..., description="widoczny prefiks klucza (daas_<8 znakow>)", examples=["daas_1a2b3c4d"])
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(..., description="pelny klucz; pokazany TYLKO w tej odpowiedzi")


# ----------------------------------------------------------------------------- uploady
class UploadOut(BaseModel):
    id: str
    kind: UploadKind
    filename: str
    bytes: int
    sha256: str
    created_at: datetime
    retention_until: datetime
    suggested_pipeline: PipelineId
    rows: int | None = None
    columns: list[str] | None = None
    mapping: dict[str, str] | None = None
    unmapped_columns: list[str] | None = None
    generated: list[str] | None = None
    warnings: list[str] | None = None
    confidence: float | None = None
    ready: bool | None = None
    auto_job_id: str | None = Field(
        default=None, description="zadanie zakolejkowane automatycznie po uploadzie (harmonogram: "
                                  "`auto_run_on_upload`); `null` = brak auto-uruchomienia albo pominiete")


# ----------------------------------------------------------------------------- zadania i runy
class JobCreate(BaseModel):
    pipeline: PipelineId = Field(..., examples=["ecommerce_demo"])
    upload_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    force_mock: bool = False
    client_name: str | None = Field(default=None, max_length=200)
    period_from: date | None = Field(
        default=None, examples=["2026-08-01"],
        description="first day of the requested report period (ISO `YYYY-MM-DD`, inclusive; not before 2000-01-01); "
                    "only rows dated on or after it are counted. `ecommerce_demo` only; `null` = no lower bound")
    period_to: date | None = Field(
        default=None, examples=["2026-08-07"],
        description="last day of the requested report period (ISO `YYYY-MM-DD`, inclusive); not before `period_from` "
                    f"and at most {PERIOD_MAX_DAYS} days after it counting both ends. `ecommerce_demo` only; "
                    "`null` = no upper bound")

    # granice wspolne z selfhost `RunRequest` (`app.core.models`): ten sam kontrakt w obu trybach
    @field_validator("period_from", "period_to")
    @classmethod
    def _period_dates(cls, value: date | None) -> date | None:
        return check_period_date(value)

    @field_validator("period_to")
    @classmethod
    def _period_order(cls, value: date | None, info: ValidationInfo) -> date | None:
        """Kolejnosc i dlugosc zakresu; blad ma `loc` = `period_to` (pole `period_from` jest juz zwalidowane)."""
        check_period_range(info.data.get("period_from"), value)
        return value


class JobOut(BaseModel):
    id: str
    pipeline: str
    status: JobStatus
    progress: int = Field(..., ge=0, le=100)
    message: str | None = None
    run_id: str | None = None
    upload_id: str | None = None
    error_code: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    period_from: date | None = Field(default=None, description="requested first day of the report period "
                                                               "(`JobCreate.period_from`); `null` = no lower bound")
    period_to: date | None = Field(default=None, description="requested last day of the report period "
                                                             "(`JobCreate.period_to`); `null` = no upper bound")


class JobPage(BaseModel):
    items: list[JobOut]
    next_cursor: str | None = Field(default=None, description="przekaz jako `cursor`, aby pobrac kolejna strone")


class RunOut(BaseModel):
    run_id: str
    pipeline: str
    status: Literal["SUCCESS", "PARTIAL", "FAILED"]
    created_at: datetime
    job_id: str | None = None
    report_sha256: dict[str, str | None]
    links: dict[str, str] = Field(default_factory=dict)
    period_from: date | None = Field(default=None, description="pierwszy dzien danych runu (ISO); `null` dla cs2_demo")
    period_to: date | None = Field(default=None, description="ostatni dzien danych runu (ISO); `null` dla cs2_demo")


class RunPage(BaseModel):
    items: list[RunOut]
    next_cursor: str | None = None


class RunDetail(RunOut):
    data_status: str | None = None
    records_processed: int | None = None
    narrative_source: str | None = None
    execution_time_sec: float | None = None
    source_detail: str | None = None
    error: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class VerifyIn(BaseModel):
    sha256: str = Field(..., examples=["a" * 64])

    @field_validator("sha256")
    @classmethod
    def _hex(cls, value: str) -> str:
        value = value.strip().lower()
        if not SHA256_RE.match(value):
            raise ValueError("sha256 must be 64 hexadecimal characters")
        return value


class VerifyOut(BaseModel):
    known: bool
    run_id: str | None = None
    pipeline: str | None = None
    created_at: datetime | None = None
    format: Literal["html", "md", "json"] | None = None


class EvidenceCheck(BaseModel):
    control_id: str
    requirement: str | None = None
    title: str | None = None
    status: str | None = None
    artifact: dict[str, Any]


class EvidenceOut(BaseModel):
    run_id: str
    run_status: Literal["SUCCESS", "PARTIAL", "FAILED"] = Field(
        ..., description="status runu kolektora; `PARTIAL` = niepelna paczka (czesc kontroli bez artefaktu)")
    host: str | None = None
    collected_at: datetime | None = None
    package_sha256: str | None = None
    integrity: str | None = None
    checks: list[EvidenceCheck]


class AuditOut(BaseModel):
    id: str
    ts: datetime
    actor_type: str
    actor_id: str | None = None
    action: str
    target: str | None = None
    ip: str | None = None
    meta: dict[str, Any]


class AuditPage(BaseModel):
    items: list[AuditOut]
    next_cursor: str | None = None


# ----------------------------------------------------------------------------- webhooki
WEBHOOK_EVENTS = ("run.finished",)


class WebhookCreate(BaseModel):
    url: str = Field(..., max_length=2000, examples=["https://hooks.example.test/daas"])
    events: list[Literal["run.finished"]] = Field(default_factory=lambda: ["run.finished"])


class WebhookOut(BaseModel):
    id: str
    url: str
    events: list[str]
    active: bool
    created_at: datetime


class WebhookCreated(WebhookOut):
    secret: str = Field(..., description="sekret podpisu (HMAC-SHA256); pokazany TYLKO w tej odpowiedzi")


# ----------------------------------------------------------------------------- usage / limity planu (A1, 10.10)
class UsagePeriod(BaseModel):
    period: str = Field(..., examples=["2026-10"], description="miesiac kalendarzowy UTC, `YYYY-MM`")
    runs: int
    upload_bytes: int


class PlanLimitsOut(BaseModel):
    runs_per_month: int | None = Field(default=None, description="`null` = bez limitu")
    upload_bytes_per_month: int | None = Field(default=None, description="`null` = bez limitu")


class UsageReport(BaseModel):
    org_id: str
    plan: str
    retention_days: int
    current: UsagePeriod
    previous: UsagePeriod
    limits: PlanLimitsOut


# ----------------------------------------------------------------------------- czlonkowie: zmiana roli (A1, 10.10)
class MemberRolePatch(BaseModel):
    role: RoleName


# ----------------------------------------------------------------------------- zaproszenia: stala odpowiedz (A1, 10.10)
class InvitationAccepted(BaseModel):
    status: Literal["accepted"] = "accepted"


# ----------------------------------------------------------------------------- logowanie: potwierdzenie (A1, 10.10)
class MagicLinkConfirm(MagicLinkVerify):
    confirm: bool = Field(default=False, description="jawne potwierdzenie logowania w tej przegladarce")


class WebhookPingOut(BaseModel):
    delivered: bool
    status: None = Field(default=None, deprecated=True,
                         description="DEPRECATED, always `null`: the exact status code of the receiver is no longer "
                                     "returned. Use `status_class`.")
    status_class: Literal["2xx", "3xx", "4xx", "5xx", "error"] = Field(
        ..., description="class of the receiver's answer; `error` = no answer (network, DNS, address refused)")


# ----------------------------------------------------------------------------- harmonogram i powiadomienia (12.10)
ChatWebhookKind = Literal["slack", "teams", "generic"]


class ScheduleIn(BaseModel):
    """Pelny stan harmonogramu (PUT zastepuje calosc; pominiete pole = wartosc domyslna). Wyjatek: para webhooka
    czatu - adres jest tylko do zapisu, wiec pominiety `notify_webhook_url` = bez zmian (`null` czysci adres),
    a pominiety `notify_webhook_kind` tez zostaje jak zapisany (bez adresu nie ma czego przestawiac)."""

    reminder_enabled: bool = Field(default=False, description="cotygodniowe przypomnienie e-mail o eksporcie")
    reminder_weekday: int = Field(default=0, ge=0, le=6, description="dzien tygodnia: 0 = poniedzialek, 6 = niedziela")
    reminder_hour: int = Field(default=8, ge=0, le=23, description="godzina (czas lokalny w `timezone`)")
    timezone: str = Field(default="Europe/Warsaw", min_length=1, max_length=64,
                          description="strefa czasowa IANA, np. `Europe/Warsaw`")
    reminder_skip_if_recent_upload: bool = Field(
        default=True, description="nie przypominaj, jesli w ostatnich 6 dniach byl upload")
    auto_run_on_upload: bool = Field(default=False, description="po uploadzie zakolejkuj zadanie dla "
                                                                "`suggested_pipeline`")
    notify_on_finish: bool = Field(default=False, description="e-mail do czlonkow po zakonczeniu runu "
                                                              "(sukces i blad)")
    notify_webhook_kind: ChatWebhookKind = Field(
        default="slack", description="format of the chat message after a run: `slack` (Incoming Webhook), `teams` "
                                     "(Workflows webhook trigger) or `generic` (plain JSON); omitted = unchanged")
    notify_webhook_url: str | None = Field(  # max_length = kolumna `org_schedules.notify_webhook_url`
        default=None, max_length=512, examples=["https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXX"],
        description="chat webhook address (write-only secret, never returned). Omitted = unchanged, `null` = remove "
                    "the address, text = set it: `https` and a public address (outside dev), no credentials; an "
                    "invalid address answers `422 validation_error` naming this field")

    @field_validator("timezone")
    @classmethod
    def _known_tz(cls, value: str) -> str:
        from app.control.schedule import is_valid_timezone

        value = value.strip()
        if not is_valid_timezone(value):
            raise ValueError("unknown IANA time zone")
        return value


class ScheduleOut(BaseModel):
    reminder_enabled: bool
    reminder_weekday: int = Field(..., ge=0, le=6)
    reminder_hour: int = Field(..., ge=0, le=23)
    timezone: str
    reminder_skip_if_recent_upload: bool
    auto_run_on_upload: bool
    notify_on_finish: bool
    notify_webhook_kind: ChatWebhookKind = Field(..., description="format of the chat message after a run")
    notify_webhook_set: bool = Field(..., description="a chat webhook address is saved (the address itself is never "
                                                      "returned)")
    notify_webhook_hint: str | None = Field(
        default=None, examples=["hooks.slack.com/\u2026XyZ9"],
        description="masked saved address: host and the last 4 characters of a long path (no query); `null` = none")
    next_reminder_at: datetime | None = Field(default=None, description="najblizsze przypomnienie (UTC); "
                                                                        "`null` = przypomnienie wylaczone")
    last_reminder_at: datetime | None = Field(default=None, description="ostatnia proba przypomnienia (UTC)")
    updated_at: datetime | None = Field(default=None, description="`null` = ustawienia domyslne, nigdy nie zapisane")


# ----------------------------------------------------------------------------- zdrowie danych (pulpit, 13.10)
HealthIssueCode = Literal["last_run_failed", "plan_limit_reached", "stale_upload", "plan_limit_near",
                          "partial_reports", "no_uploads_yet", "reminder_off", "auto_run_off"]
HealthSeverity = Literal["error", "warning", "info"]


class WeekHealth(BaseModel):
    week_start: date = Field(..., description="Monday of the ISO week (local date in `timezone`)")
    partial: bool = Field(..., description="`true` for the current week, which is still running")
    uploads: int = Field(..., ge=0, description="files uploaded in the week")
    upload_bytes: int = Field(..., ge=0, description="bytes uploaded in the week")
    runs_succeeded: int = Field(..., ge=0, description="jobs that finished `succeeded` in the week")
    runs_failed: int = Field(..., ge=0, description="jobs that finished `failed` in the week (`no_records` included)")
    reports: int = Field(..., ge=0, description="reports delivered in the week: runs `SUCCESS` or `PARTIAL` "
                                                "whose job did not fail")
    avg_run_seconds: float | None = Field(..., description="mean `finished_at - started_at` of the finished jobs "
                                                           "(seconds); `null` = no finished job")
    last_report_at: datetime | None = Field(..., description="latest report of the week; `null` = none")


class DataHealthSummary(BaseModel):
    last_upload_at: datetime | None = Field(..., description="latest upload still on record; `null` = none")
    last_success_at: datetime | None = Field(..., description="latest job that finished `succeeded`")
    last_failure_at: datetime | None = Field(..., description="latest job that finished `failed`")
    last_error_code: str | None = Field(..., description="`error_code` of that failed job")
    streak_weeks: int = Field(..., ge=0, description="finished weeks in a row with at least one report, counted back "
                                                     "from last week (the current week is not counted; at most 25)")
    reminder_enabled: bool = Field(..., description="weekly export reminder (schedule)")
    next_reminder_at: datetime | None = Field(..., description="next reminder (UTC); `null` = reminder off")
    auto_run_on_upload: bool = Field(..., description="a job starts right after each upload (schedule)")
    notify_on_finish: bool = Field(..., description="members get an e-mail when a run finishes (schedule)")
    plan: str = Field(..., description="plan of the organization")
    runs_used: int = Field(..., ge=0, description="runs counted this calendar month (UTC)")
    runs_limit: int | None = Field(..., description="monthly run limit of the plan; `null` = unlimited")
    retention_days: int = Field(..., description="how long uploaded files are kept")


class HealthIssue(BaseModel):
    code: HealthIssueCode
    severity: HealthSeverity
    since: datetime | None = Field(..., description="when the condition started, if known")
    link: str = Field(..., description="cockpit path that explains or fixes the issue, e.g. `/upload`")


class DataHealthOut(BaseModel):
    generated_at: datetime
    timezone: str = Field(..., description="IANA zone of the schedule; weeks start on Monday 00:00 there")
    weeks: list[WeekHealth] = Field(..., description="oldest first; the last one is the current, partial week")
    summary: DataHealthSummary
    issues: list[HealthIssue] = Field(..., description="most severe first: `error`, `warning`, `info`")

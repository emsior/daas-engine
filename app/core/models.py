"""Modele domenowe Pydantic: żądania, wyniki runu, struktury CS2 i E-Commerce."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PipelineName = Literal["cs2_demo", "ecommerce_demo"]


class DataStatus(str, Enum):
    LIVE_DATA = "LIVE_DATA"
    MOCK_DATA = "MOCK_DATA"
    BLOCKED_MISSING_SECRET = "BLOCKED_MISSING_SECRET"  # noqa: S105  nazwa statusu, nie sekret


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


# ---------------------------------------------------------------------------
# API: żądania / odpowiedzi
# ---------------------------------------------------------------------------
class RunRequest(BaseModel):
    pipeline: PipelineName = Field(..., description="Nazwa pipeline'u do uruchomienia")
    force_mock: bool = Field(default=False, description="Wymuś fixtures nawet gdy sekret istnieje")
    notify: bool = Field(default=False, description="Wyślij podsumowanie na Discord (jeśli webhook skonfigurowany)")
    source_path: str | None = Field(
        default=None,
        description="Ścieżka do pliku klienta (z POST /upload) — ma priorytet nad API i fixtures; wynik = LIVE_DATA",
    )
    client_name: str | None = Field(default=None, description="Nazwa klienta do nagłówka raportu")


class UploadResponse(BaseModel):
    upload_id: str
    source_path: str
    filename: str
    rows: int
    columns: list[str]
    mapping: dict[str, str]
    unmapped_columns: list[str]
    generated: list[str]
    warnings: list[str]
    confidence: float
    ready: bool
    run_payload: dict[str, Any]


class RunResult(BaseModel):
    run_id: str
    pipeline: str
    run_status: RunStatus
    data_status: DataStatus
    records_processed: int = 0
    report_path: str | None = None
    report_json_path: str | None = None
    report_html_path: str | None = None
    narrative_source: Literal["LLM", "TEMPLATE"] | None = None
    client_name: str | None = None
    execution_time_sec: float = 0.0
    started_at: datetime
    finished_at: datetime
    source_detail: str | None = None
    error: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class PipelineInfo(BaseModel):
    name: str
    description: str
    domain: str
    required_secrets: list[str]
    secrets_present: bool
    expected_data_status: DataStatus


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    app: str
    version: str
    env: str
    duckdb_path: str
    secrets: dict[str, bool]
    timestamp: datetime


# ---------------------------------------------------------------------------
# CS2
# ---------------------------------------------------------------------------
class CS2Match(BaseModel):
    match_id: str
    played_at: datetime
    map: str
    team: str
    opponent: str
    result: Literal["WIN", "LOSS", "TIE"]
    score_team: int
    score_opponent: int
    kills: int = Field(ge=0)
    deaths: int = Field(ge=0)
    assists: int = Field(ge=0)
    headshots: int = Field(ge=0)
    adr: float = Field(ge=0, description="Average Damage per Round")
    rating: float = Field(ge=0, description="HLTV-like rating 2.0")
    rounds_played: int = Field(ge=1)
    mvps: int = Field(default=0, ge=0)

    @property
    def kd(self) -> float:
        return round(self.kills / self.deaths, 3) if self.deaths else float(self.kills)

    @property
    def hs_pct(self) -> float:
        return round(100 * self.headshots / self.kills, 2) if self.kills else 0.0


class CS2Metrics(BaseModel):
    matches: int
    wins: int
    losses: int
    ties: int
    win_rate: float
    kills: int
    deaths: int
    assists: int
    kd_ratio: float
    avg_rating: float
    avg_adr: float
    hs_pct: float
    form_score: float = Field(description="0-100, ważona forma z ostatnich meczów")
    form_trend: Literal["UP", "FLAT", "DOWN"]
    best_map: str | None
    worst_map: str | None
    map_breakdown: list[dict[str, Any]]
    last5: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# E-Commerce
# ---------------------------------------------------------------------------
class EcommerceOrder(BaseModel):
    order_id: str
    date: date
    product: str
    category: str
    revenue: float = Field(ge=0)
    cost: float = Field(ge=0)
    profit: float
    discount: float = Field(ge=0, le=1, description="udział rabatu 0-1")
    status: Literal["completed", "refunded", "cancelled", "pending"]
    quantity: int = Field(default=1, ge=1)

    @field_validator("status", mode="before")
    @classmethod
    def _norm_status(cls, v: Any) -> Any:
        return str(v).strip().lower() if v is not None else v


class EcommerceMetrics(BaseModel):
    orders_total: int
    orders_completed: int
    orders_refunded: int
    orders_cancelled: int
    revenue_total: float
    cost_total: float
    profit_total: float
    margin_pct: float
    avg_order_value: float
    avg_discount_pct: float
    loss_orders: list[dict[str, Any]]
    loss_orders_count: int
    loss_orders_total: float
    top_categories: list[dict[str, Any]]
    top_products: list[dict[str, Any]]
    anomalies: list[dict[str, Any]]
    weekly: list[dict[str, Any]] = []          # per tydzień ISO: orders, revenue, profit, margin_pct, aov
    wow: dict[str, Any] | None = None          # ostatni tydzień vs poprzedni (delty); None gdy < 2 tygodni

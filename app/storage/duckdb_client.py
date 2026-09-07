"""Klient DuckDB: inicjalizacja schematów, idempotentny zapis, odczyt metadanych runów.

Jeden plik bazy współdzielony przez wszystkie pipeline'y (runtime/daas.duckdb).
Każdy zapis jest idempotentny: rekordy są kluczowane (run_id, match_id/order_id)
i wstawiane przez INSERT OR REPLACE.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

import duckdb
import pandas as pd

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id              VARCHAR PRIMARY KEY,
    pipeline            VARCHAR NOT NULL,
    run_status          VARCHAR NOT NULL,
    data_status         VARCHAR NOT NULL,
    records_processed   INTEGER DEFAULT 0,
    report_path         VARCHAR,
    report_json_path    VARCHAR,
    report_html_path    VARCHAR,
    narrative_source    VARCHAR,
    client_name         VARCHAR,
    execution_time_sec  DOUBLE DEFAULT 0,
    started_at          TIMESTAMP NOT NULL,
    finished_at         TIMESTAMP,
    source_detail       VARCHAR,
    error               VARCHAR,
    metrics_json        VARCHAR
);

CREATE TABLE IF NOT EXISTS cs2_matches (
    run_id          VARCHAR NOT NULL,
    match_id        VARCHAR NOT NULL,
    played_at       TIMESTAMP,
    map             VARCHAR,
    team            VARCHAR,
    opponent        VARCHAR,
    result          VARCHAR,
    score_team      INTEGER,
    score_opponent  INTEGER,
    kills           INTEGER,
    deaths          INTEGER,
    assists         INTEGER,
    headshots       INTEGER,
    adr             DOUBLE,
    rating          DOUBLE,
    rounds_played   INTEGER,
    mvps            INTEGER,
    data_status     VARCHAR,
    PRIMARY KEY (run_id, match_id)
);

CREATE TABLE IF NOT EXISTS ecommerce_orders (
    run_id      VARCHAR NOT NULL,
    order_id    VARCHAR NOT NULL,
    order_date  DATE,
    product     VARCHAR,
    category    VARCHAR,
    revenue     DOUBLE,
    cost        DOUBLE,
    profit      DOUBLE,
    discount    DOUBLE,
    status      VARCHAR,
    quantity    INTEGER,
    data_status VARCHAR,
    PRIMARY KEY (run_id, order_id)
);
"""

CS2_COLUMNS = [
    "run_id", "match_id", "played_at", "map", "team", "opponent", "result",
    "score_team", "score_opponent", "kills", "deaths", "assists", "headshots",
    "adr", "rating", "rounds_played", "mvps", "data_status",
]
ECOM_COLUMNS = [
    "run_id", "order_id", "order_date", "product", "category", "revenue", "cost",
    "profit", "discount", "status", "quantity", "data_status",
]


class DuckDBClient:
    """Cienki wrapper na duckdb z blokadą procesową (DuckDB = jeden writer)."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.init_schema()

    # ------------------------------------------------------------------
    @contextmanager
    def connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with self._lock:
            con = duckdb.connect(str(self.db_path))
            try:
                yield con
            finally:
                con.close()

    def init_schema(self) -> None:
        with self.connect() as con:
            for stmt in [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]:
                con.execute(stmt)
            # migracje dla baz utworzonych przez v0.1
            for col in ("report_html_path", "narrative_source", "client_name"):
                con.execute(f"ALTER TABLE runs ADD COLUMN IF NOT EXISTS {col} VARCHAR")

    # ------------------------------------------------------------------
    # RUNS
    # ------------------------------------------------------------------
    def upsert_run(self, run: dict[str, Any]) -> None:
        metrics = run.get("metrics") or {}
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO runs
                (run_id, pipeline, run_status, data_status, records_processed, report_path,
                 report_json_path, report_html_path, narrative_source, client_name,
                 execution_time_sec, started_at, finished_at, source_detail, error, metrics_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run["run_id"],
                    run["pipeline"],
                    _enum_val(run["run_status"]),
                    _enum_val(run["data_status"]),
                    int(run.get("records_processed") or 0),
                    run.get("report_path"),
                    run.get("report_json_path"),
                    run.get("report_html_path"),
                    run.get("narrative_source"),
                    run.get("client_name"),
                    float(run.get("execution_time_sec") or 0.0),
                    run["started_at"],
                    run.get("finished_at"),
                    run.get("source_detail"),
                    run.get("error"),
                    json.dumps(metrics, default=str, ensure_ascii=False),
                ],
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            df = con.execute("SELECT * FROM runs WHERE run_id = ?", [run_id]).df()
        return _row_to_run(df.iloc[0]) if len(df) else None

    def latest_run(self, pipeline: str | None = None) -> dict[str, Any] | None:
        with self.connect() as con:
            if pipeline:
                df = con.execute(
                    "SELECT * FROM runs WHERE pipeline = ? ORDER BY started_at DESC LIMIT 1", [pipeline]
                ).df()
            else:
                df = con.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").df()
        return _row_to_run(df.iloc[0]) if len(df) else None

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as con:
            df = con.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", [limit]).df()
        return [_row_to_run(r) for _, r in df.iterrows()]

    # ------------------------------------------------------------------
    # CS2
    # ------------------------------------------------------------------
    def write_cs2_matches(self, run_id: str, rows: Iterable[dict[str, Any]], data_status: str) -> int:
        df = pd.DataFrame(list(rows))
        if df.empty:
            return 0
        df["run_id"] = run_id
        df["data_status"] = data_status
        df = df.reindex(columns=CS2_COLUMNS)
        df["played_at"] = pd.to_datetime(df["played_at"], utc=True).dt.tz_localize(None)
        with self.connect() as con:
            con.register("_cs2_tmp", df)
            con.execute(f"INSERT OR REPLACE INTO cs2_matches SELECT {', '.join(CS2_COLUMNS)} FROM _cs2_tmp")
            con.unregister("_cs2_tmp")
        return int(len(df))

    def read_cs2_matches(self, run_id: str) -> pd.DataFrame:
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM cs2_matches WHERE run_id = ? ORDER BY played_at", [run_id]
            ).df()

    # ------------------------------------------------------------------
    # E-COMMERCE
    # ------------------------------------------------------------------
    def write_ecommerce_orders(self, run_id: str, rows: Iterable[dict[str, Any]], data_status: str) -> int:
        df = pd.DataFrame(list(rows))
        if df.empty:
            return 0
        if "date" in df.columns and "order_date" not in df.columns:
            df = df.rename(columns={"date": "order_date"})
        df["run_id"] = run_id
        df["data_status"] = data_status
        df = df.reindex(columns=ECOM_COLUMNS)
        df["order_date"] = pd.to_datetime(df["order_date"]).dt.date
        with self.connect() as con:
            con.register("_ecom_tmp", df)
            con.execute(f"INSERT OR REPLACE INTO ecommerce_orders SELECT {', '.join(ECOM_COLUMNS)} FROM _ecom_tmp")
            con.unregister("_ecom_tmp")
        return int(len(df))

    def read_ecommerce_orders(self, run_id: str) -> pd.DataFrame:
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM ecommerce_orders WHERE run_id = ? ORDER BY order_date", [run_id]
            ).df()

    # ------------------------------------------------------------------
    def table_counts(self) -> dict[str, int]:
        with self.connect() as con:
            return {
                t: int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                for t in ("runs", "cs2_matches", "ecommerce_orders")
            }


# ----------------------------------------------------------------------
def _enum_val(v: Any) -> str:
    return getattr(v, "value", v)


def _row_to_run(row: pd.Series) -> dict[str, Any]:
    d = {k: (None if _is_nan(v) else v) for k, v in row.to_dict().items()}
    for k in ("started_at", "finished_at"):
        if isinstance(d.get(k), pd.Timestamp):
            d[k] = d[k].to_pydatetime()
    try:
        d["metrics"] = json.loads(d.pop("metrics_json") or "{}")
    except (TypeError, ValueError):
        d["metrics"] = {}
    if isinstance(d.get("records_processed"), float):
        d["records_processed"] = int(d["records_processed"])
    return d


def _is_nan(v: Any) -> bool:
    try:
        return v is not None and not isinstance(v, (str, bytes, dict, list)) and pd.isna(v)
    except (TypeError, ValueError):
        return False


def utcnow() -> datetime:
    return datetime.utcnow()

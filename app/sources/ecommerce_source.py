"""Adapter źródła e-commerce.

Priorytet:
1. Apify dataset (APIFY_TOKEN + APIFY_DATASET_ID w env) -> LIVE_DATA
2. Zewnętrzny CSV wskazany w ECOMMERCE_CSV_PATH -> LIVE_DATA (klienckie dane)
3. Fallback: data/fixtures/ecommerce_orders.csv -> MOCK_DATA
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from app.core.config import Settings
from app.core.models import DataStatus, EcommerceOrder
from app.sources.cs2_source import SourceResult

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["order_id", "date", "product", "category", "revenue", "cost", "profit", "discount", "status"]


class EcommerceSource:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.fixture_path: Path = settings.fixtures_path / "ecommerce_orders.csv"

    # ------------------------------------------------------------------
    def fetch(self, force_mock: bool = False, source_path: str | None = None) -> SourceResult:
        if force_mock:
            return self._load_csv(self.fixture_path, DataStatus.MOCK_DATA, "force_mock=true")

        # 0) plik klienta (z POST /upload) — najwyższy priorytet
        #    SECURITY: canonicalization + sprawdzenie, że plik jest w uploads/
        if source_path:
            uploads_root = (self.settings.reports_path.parent / "uploads").resolve()
            p = Path(source_path).resolve()
            if not p.is_relative_to(uploads_root.resolve()):
                return SourceResult(
                    records=[],
                    data_status=DataStatus.BLOCKED_MISSING_SECRET,
                    detail="source_path outside allowed uploads directory",
                )
            if not p.exists():
                return SourceResult(records=[], data_status=DataStatus.BLOCKED_MISSING_SECRET,
                                    detail="source_path not found")
            return self._load_client_file(p)

        dataset_id = os.getenv("APIFY_DATASET_ID", "").strip()
        if self.settings.has_apify and dataset_id:
            try:
                return self._fetch_apify(dataset_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("Apify fetch failed (%s) -> next source", exc)

        external = os.getenv("ECOMMERCE_CSV_PATH", "").strip()
        if external and Path(external).exists():
            try:
                return self._load_csv(Path(external), DataStatus.LIVE_DATA, f"external csv: {external}")
            except Exception as exc:  # noqa: BLE001
                log.warning("External CSV failed (%s) -> fixture fallback", exc)

        reason = "APIFY_TOKEN missing" if not self.settings.has_apify else "APIFY_DATASET_ID missing"
        return self._load_csv(self.fixture_path, DataStatus.MOCK_DATA, f"{reason} -> fixture fallback")

    # ------------------------------------------------------------------
    def _load_csv(self, path: Path, status: DataStatus, detail: str) -> SourceResult:
        if not path.exists():
            return SourceResult(records=[], data_status=DataStatus.BLOCKED_MISSING_SECRET,
                                detail=f"{detail}; csv not found at {path}")
        df = pd.read_csv(path)
        return SourceResult(
            records=_normalize(df),
            data_status=status,
            detail=detail,
            raw_meta={"path": str(path), "rows": int(len(df))},
        )

    def _load_client_file(self, path: Path) -> SourceResult:
        """CSV/XLSX klienta przez mapper kolumn (PL/EN, format liczb, statusy)."""
        from app.sources.csv_mapper import map_dataframe, read_client_csv

        if path.suffix.lower() in {".xlsx", ".xls"}:
            raw = pd.read_excel(path, dtype=str)
        else:
            raw = read_client_csv(str(path))
        mapped, rep = map_dataframe(raw)
        records = [EcommerceOrder.model_validate(r).model_dump() for r in mapped.to_dict(orient="records")]
        detail = f"client file: {path.name} ({rep.rows_out}/{rep.rows_in} rows, mapping confidence {rep.confidence})"
        if rep.warnings:
            detail += " | " + "; ".join(rep.warnings)
        return SourceResult(records=records, data_status=DataStatus.LIVE_DATA, detail=detail,
                            raw_meta={"path": str(path), "mapping": rep.to_dict()})

    def _fetch_apify(self, dataset_id: str) -> SourceResult:
        url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
        with httpx.Client(timeout=self.settings.source_timeout_sec) as client:
            r = client.get(url, params={"token": self.settings.apify_token, "format": "json", "clean": "true"})
            r.raise_for_status()
            df = pd.DataFrame(r.json())
        return SourceResult(
            records=_normalize(df),
            data_status=DataStatus.LIVE_DATA,
            detail=f"apify dataset {dataset_id}",
            raw_meta={"rows": int(len(df))},
        )


def _normalize(df: pd.DataFrame) -> list[dict[str, Any]]:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns and c != "profit"]
    if missing:
        raise ValueError(f"CSV brakuje kolumn: {missing}")
    if "quantity" not in df.columns:
        df["quantity"] = 1
    if "profit" not in df.columns:
        df["profit"] = df["revenue"].astype(float) - df["cost"].astype(float)
    df["discount"] = df["discount"].fillna(0).astype(float)
    # rabat podany w procentach (np. 20) -> ułamek
    df.loc[df["discount"] > 1, "discount"] = df.loc[df["discount"] > 1, "discount"] / 100.0
    df["date"] = pd.to_datetime(df["date"]).dt.date
    records: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        records.append(EcommerceOrder.model_validate(row).model_dump())
    return records

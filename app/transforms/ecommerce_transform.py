"""Agregacje analityczne E-Commerce: przychód, marża, anomalie stratne, top kategorie."""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.models import EcommerceMetrics

# Statusy liczone do przychodu/zysku (refund/cancel wykluczone z P&L, ale raportowane).
REVENUE_STATUSES = {"completed", "pending"}


def detect_anomalies(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Wykrywanie anomalii:
    - NEGATIVE_PROFIT: zysk < 0
    - DEEP_DISCOUNT: rabat >= 25%
    - MARGIN_OUTLIER: marża poniżej (mediana - 2*MAD) w danej kategorii
    - REVENUE_OUTLIER: przychód > Q3 + 1.5*IQR
    """
    if df.empty:
        return []
    out: list[dict[str, Any]] = []
    df = df.copy()
    df["margin_pct"] = df.apply(lambda r: 100 * r["profit"] / r["revenue"] if r["revenue"] else 0.0, axis=1)

    def add(row: pd.Series, kind: str, note: str) -> None:
        out.append({
            "order_id": row["order_id"], "type": kind, "product": row["product"],
            "category": row["category"], "revenue": round(float(row["revenue"]), 2),
            "profit": round(float(row["profit"]), 2), "discount_pct": round(100 * float(row["discount"]), 1),
            "note": note,
        })

    for _, r in df[df["profit"] < 0].iterrows():
        add(r, "NEGATIVE_PROFIT", f"strata {r['profit']:.2f} przy rabacie {100*r['discount']:.0f}%")
    for _, r in df[(df["discount"] >= 0.25) & (df["profit"] >= 0)].iterrows():
        add(r, "DEEP_DISCOUNT", f"rabat {100*r['discount']:.0f}% zjada marżę do {r['margin_pct']:.1f}%")

    for cat, g in df.groupby("category"):
        if len(g) < 3:
            continue
        med = g["margin_pct"].median()
        mad = (g["margin_pct"] - med).abs().median() or 1.0
        thr = med - 2 * mad
        for _, r in g[(g["margin_pct"] < thr) & (g["profit"] >= 0)].iterrows():
            add(r, "MARGIN_OUTLIER", f"marża {r['margin_pct']:.1f}% vs mediana kategorii {med:.1f}%")

    q1, q3 = df["revenue"].quantile([0.25, 0.75])
    hi = q3 + 1.5 * (q3 - q1)
    for _, r in df[df["revenue"] > hi].iterrows():
        add(r, "REVENUE_OUTLIER", f"przychód {r['revenue']:.2f} > próg {hi:.2f}")
    return out


def transform_ecommerce(records: list[dict[str, Any]]) -> EcommerceMetrics:
    df = pd.DataFrame(records)
    if df.empty:
        return EcommerceMetrics(
            orders_total=0, orders_completed=0, orders_refunded=0, orders_cancelled=0,
            revenue_total=0.0, cost_total=0.0, profit_total=0.0, margin_pct=0.0, avg_order_value=0.0,
            avg_discount_pct=0.0, loss_orders=[], loss_orders_count=0, loss_orders_total=0.0,
            top_categories=[], top_products=[], anomalies=[],
        )
    df["status"] = df["status"].str.lower()
    pnl = df[df["status"].isin(REVENUE_STATUSES)].copy()

    revenue = float(pnl["revenue"].sum())
    cost = float(pnl["cost"].sum())
    profit = float(pnl["profit"].sum())

    loss = pnl[pnl["profit"] < 0].sort_values("profit")
    loss_rows = loss[["order_id", "date", "product", "category", "revenue", "cost", "profit", "discount"]].copy()
    loss_rows["date"] = loss_rows["date"].astype(str)
    loss_rows = loss_rows.round(2)

    by_cat = (
        pnl.groupby("category")
        .agg(orders=("order_id", "count"), revenue=("revenue", "sum"), profit=("profit", "sum"))
        .reset_index()
    )
    by_cat["margin_pct"] = (100 * by_cat["profit"] / by_cat["revenue"]).round(1)
    by_cat["revenue_share_pct"] = (100 * by_cat["revenue"] / revenue).round(1) if revenue else 0.0
    by_cat = by_cat.sort_values("revenue", ascending=False).round(2)

    by_prod = (
        pnl.groupby("product")
        .agg(orders=("order_id", "count"), units=("quantity", "sum"), revenue=("revenue", "sum"), profit=("profit", "sum"))
        .reset_index()
        .sort_values("revenue", ascending=False)
        .head(5)
        .round(2)
    )

    return EcommerceMetrics(
        orders_total=int(len(df)),
        orders_completed=int((df["status"] == "completed").sum()),
        orders_refunded=int((df["status"] == "refunded").sum()),
        orders_cancelled=int((df["status"] == "cancelled").sum()),
        revenue_total=round(revenue, 2),
        cost_total=round(cost, 2),
        profit_total=round(profit, 2),
        margin_pct=round(100 * profit / revenue, 2) if revenue else 0.0,
        avg_order_value=round(revenue / len(pnl), 2) if len(pnl) else 0.0,
        avg_discount_pct=round(100 * float(pnl["discount"].mean()), 2) if len(pnl) else 0.0,
        loss_orders=loss_rows.to_dict(orient="records"),
        loss_orders_count=int(len(loss)),
        loss_orders_total=round(float(loss["profit"].sum()), 2),
        top_categories=by_cat.to_dict(orient="records"),
        top_products=by_prod.to_dict(orient="records"),
        anomalies=detect_anomalies(pnl),
    )

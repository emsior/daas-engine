"""Agregacje analityczne CS2: K/D, win rate, rating, ADR, form score."""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.models import CS2Metrics

# Wagi form score (suma = 1.0). Kalibracja: rating 1.00 / KD 1.00 / ADR 75 / WR 50% ≈ 50 pkt.
W_RATING, W_KD, W_ADR, W_WR = 0.35, 0.25, 0.20, 0.20


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def compute_form_score(df: pd.DataFrame, last_n: int = 5) -> float:
    """Form score 0-100 z wykładniczym ważeniem najnowszych meczów.

    Składowe (znormalizowane do 0-1 przy sensownych pułapach):
    rating/1.6, kd/2.0, adr/120, win(1/0). Ostatni mecz waży najwięcej.
    """
    if df.empty:
        return 0.0
    recent = df.sort_values("played_at").tail(last_n).reset_index(drop=True)
    n = len(recent)
    weights = pd.Series([0.6 ** (n - 1 - i) for i in range(n)])
    weights = weights / weights.sum()
    kd = recent.apply(lambda r: _safe_div(r["kills"], r["deaths"], float(r["kills"])), axis=1)
    win = (recent["result"] == "WIN").astype(float) + 0.5 * (recent["result"] == "TIE").astype(float)
    component = (
        W_RATING * (recent["rating"] / 1.6).clip(0, 1)
        + W_KD * (kd / 2.0).clip(0, 1)
        + W_ADR * (recent["adr"] / 120.0).clip(0, 1)
        + W_WR * win
    )
    return round(float((component * weights).sum() * 100), 1)


def _trend(df: pd.DataFrame) -> str:
    if len(df) < 4:
        return "FLAT"
    s = df.sort_values("played_at")["rating"]
    half = len(s) // 2
    diff = s.iloc[half:].mean() - s.iloc[:half].mean()
    return "UP" if diff > 0.05 else "DOWN" if diff < -0.05 else "FLAT"


def transform_cs2(records: list[dict[str, Any]]) -> CS2Metrics:
    df = pd.DataFrame(records)
    if df.empty:
        return CS2Metrics(
            matches=0, wins=0, losses=0, ties=0, win_rate=0.0, kills=0, deaths=0, assists=0,
            kd_ratio=0.0, avg_rating=0.0, avg_adr=0.0, hs_pct=0.0, form_score=0.0, form_trend="FLAT",
            best_map=None, worst_map=None, map_breakdown=[], last5=[],
        )
    df["played_at"] = pd.to_datetime(df["played_at"])
    df = df.sort_values("played_at").reset_index(drop=True)

    wins = int((df["result"] == "WIN").sum())
    losses = int((df["result"] == "LOSS").sum())
    ties = int((df["result"] == "TIE").sum())
    kills, deaths, assists = int(df["kills"].sum()), int(df["deaths"].sum()), int(df["assists"].sum())
    headshots = int(df["headshots"].sum())

    df["win"] = (df["result"] == "WIN").astype(int)
    df["kd"] = df.apply(lambda r: _safe_div(r["kills"], r["deaths"], float(r["kills"])), axis=1)
    by_map = (
        df.groupby("map")
        .agg(matches=("match_id", "count"), win_rate=("win", "mean"), avg_rating=("rating", "mean"),
             avg_adr=("adr", "mean"), kd=("kd", "mean"))
        .reset_index()
        .sort_values(["win_rate", "avg_rating"], ascending=False)
    )
    by_map["win_rate"] = (by_map["win_rate"] * 100).round(1)
    by_map[["avg_rating", "avg_adr", "kd"]] = by_map[["avg_rating", "avg_adr", "kd"]].round(2)

    last5 = df.tail(5)[["match_id", "played_at", "map", "opponent", "result", "score_team", "score_opponent",
                        "kills", "deaths", "rating", "adr"]].copy()
    last5["played_at"] = last5["played_at"].dt.strftime("%Y-%m-%d")

    return CS2Metrics(
        matches=int(len(df)),
        wins=wins,
        losses=losses,
        ties=ties,
        win_rate=round(100 * _safe_div(wins, len(df)), 1),
        kills=kills,
        deaths=deaths,
        assists=assists,
        kd_ratio=round(_safe_div(kills, deaths, float(kills)), 2),
        avg_rating=round(float(df["rating"].mean()), 2),
        avg_adr=round(float(df["adr"].mean()), 1),
        hs_pct=round(100 * _safe_div(headshots, kills), 1),
        form_score=compute_form_score(df),
        form_trend=_trend(df),
        best_map=str(by_map.iloc[0]["map"]) if len(by_map) else None,
        worst_map=str(by_map.iloc[-1]["map"]) if len(by_map) else None,
        map_breakdown=by_map.to_dict(orient="records"),
        last5=last5.to_dict(orient="records"),
    )

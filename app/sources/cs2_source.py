"""Adapter źródła CS2.

Priorytet:
1. FACEIT Data API (jeśli FACEIT_API_KEY + FACEIT_PLAYER_NICKNAME) -> LIVE_DATA
2. Fallback: data/fixtures/cs2_matches.json -> MOCK_DATA

Każdy błąd sieci / parsowania przy LIVE = łagodny fallback na fixtures
(pipeline nigdy nie wywraca się z powodu źródła).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings
from app.core.models import CS2Match, DataStatus

log = logging.getLogger(__name__)

FACEIT_BASE = "https://open.faceit.com/data/v4"


@dataclass
class SourceResult:
    records: list[dict[str, Any]]
    data_status: DataStatus
    detail: str
    raw_meta: dict[str, Any] = field(default_factory=dict)


class CS2Source:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.fixture_path: Path = settings.fixtures_path / "cs2_matches.json"

    # ------------------------------------------------------------------
    def fetch(self, force_mock: bool = False, limit: int = 20) -> SourceResult:
        if force_mock:
            return self._load_fixture(detail="force_mock=true")
        if not self.settings.has_faceit:
            return self._load_fixture(detail="FACEIT_API_KEY missing -> fixture fallback")
        if not self.settings.faceit_player_nickname:
            return self._load_fixture(detail="FACEIT_PLAYER_NICKNAME missing -> fixture fallback")
        try:
            return self._fetch_live(limit=limit)
        except Exception as exc:  # noqa: BLE001 — świadomie: nigdy nie wywracamy pipeline'u
            log.warning("FACEIT live fetch failed (%s) -> fixture fallback", exc)
            return self._load_fixture(detail=f"live fetch failed: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    def _load_fixture(self, detail: str) -> SourceResult:
        if not self.fixture_path.exists():
            return SourceResult(
                records=[],
                data_status=DataStatus.BLOCKED_MISSING_SECRET,
                detail=f"{detail}; fixture not found at {self.fixture_path}",
            )
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        raw = payload["matches"] if isinstance(payload, dict) else payload
        records = [CS2Match.model_validate(r).model_dump() for r in raw]
        return SourceResult(
            records=records,
            data_status=DataStatus.MOCK_DATA,
            detail=detail,
            raw_meta={"fixture": str(self.fixture_path), "player": payload.get("player") if isinstance(payload, dict) else None},
        )

    # ------------------------------------------------------------------
    def _fetch_live(self, limit: int) -> SourceResult:
        headers = {"Authorization": f"Bearer {self.settings.faceit_api_key}", "Accept": "application/json"}
        nick = self.settings.faceit_player_nickname
        with httpx.Client(base_url=FACEIT_BASE, headers=headers, timeout=self.settings.source_timeout_sec) as client:
            player = client.get("/players", params={"nickname": nick}).raise_for_status().json()
            player_id = player["player_id"]
            hist = client.get(
                f"/players/{player_id}/history", params={"game": "cs2", "offset": 0, "limit": limit}
            ).raise_for_status().json()
            records: list[dict[str, Any]] = []
            for item in hist.get("items", []):
                match_id = item["match_id"]
                stats = client.get(f"/matches/{match_id}/stats").raise_for_status().json()
                parsed = _parse_faceit_match(item, stats, player_id)
                if parsed:
                    records.append(CS2Match.model_validate(parsed).model_dump())
        return SourceResult(
            records=records,
            data_status=DataStatus.LIVE_DATA,
            detail=f"FACEIT live: player={nick}, matches={len(records)}",
            raw_meta={"player_id": player_id},
        )


def _parse_faceit_match(item: dict[str, Any], stats: dict[str, Any], player_id: str) -> dict[str, Any] | None:
    """Mapuje odpowiedź FACEIT match stats na CS2Match. Zwraca None gdy gracz nie znaleziony."""
    rounds = stats.get("rounds") or []
    if not rounds:
        return None
    rnd = rounds[0]
    round_stats = rnd.get("round_stats", {})
    teams = rnd.get("teams", [])
    my_team, my_player = None, None
    for t in teams:
        for p in t.get("players", []):
            if p.get("player_id") == player_id:
                my_team, my_player = t, p
    if my_team is None or my_player is None:
        return None
    opp_team = next((t for t in teams if t is not my_team), {})
    ps = my_player.get("player_stats", {})
    ts = my_team.get("team_stats", {})
    os_ = opp_team.get("team_stats", {})
    score_team = int(ts.get("Final Score", 0))
    score_opp = int(os_.get("Final Score", 0))
    result = "WIN" if score_team > score_opp else "LOSS" if score_team < score_opp else "TIE"
    kills = int(ps.get("Kills", 0))
    deaths = int(ps.get("Deaths", 0))
    rounds_played = int(round_stats.get("Rounds", score_team + score_opp) or 1)
    adr = float(ps.get("ADR", 0) or 0)
    played_at = datetime.fromtimestamp(int(item.get("finished_at", 0)), tz=timezone.utc)
    return {
        "match_id": item["match_id"],
        "played_at": played_at,
        "map": round_stats.get("Map", "unknown"),
        "team": my_team.get("team_stats", {}).get("Team", "me"),
        "opponent": opp_team.get("team_stats", {}).get("Team", "opponent"),
        "result": result,
        "score_team": score_team,
        "score_opponent": score_opp,
        "kills": kills,
        "deaths": deaths,
        "assists": int(ps.get("Assists", 0)),
        "headshots": int(ps.get("Headshots", 0)),
        "adr": adr,
        "rating": _approx_rating(kills, deaths, adr, rounds_played),
        "rounds_played": rounds_played,
        "mvps": int(ps.get("MVPs", 0)),
    }


def _approx_rating(kills: int, deaths: int, adr: float, rounds: int) -> float:
    """Przybliżenie ratingu (FACEIT nie zwraca HLTV 2.0): mix KPR, DPR, ADR."""
    rounds = max(rounds, 1)
    kpr, dpr = kills / rounds, deaths / rounds
    return round(0.0073 * adr + 0.3591 * kpr - 0.5329 * dpr + 0.2372 + 0.0032 * (kills - deaths), 2)

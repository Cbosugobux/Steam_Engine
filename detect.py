import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pandas as pd


@dataclass
class Trigger:
    sport: str
    event_id: str
    commence_time_utc: str
    home_team: str
    away_team: str
    market: str
    outcome: str

    origin_book: str
    origin_move: float

    current_best_line: float
    current_best_book: str

    ts_utc: str
    notes: str

    score: float
    minutes_to_game: float
    steam_strength: float
    sharp_confirmations: int
    followers: int
    followers_list: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_key(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def trigger_key_hash(tr: Trigger) -> str:
    return _hash_key(
        tr.sport, tr.event_id, tr.market, tr.outcome,
        tr.origin_book, f"{tr.current_best_line:.3f}"
    )


def detect_triggers(con: sqlite3.Connection, sport: str, market: str, config: Dict) -> List[Trigger]:
    lookback = float(config.get("lookback_hours", 24))
    t0 = (_utc_now() - timedelta(hours=lookback)).isoformat()

    df = pd.read_sql_query(
        """
        SELECT ts_utc, sport, event_id, commence_time_utc, home_team, away_team,
               bookmaker_key, market, outcome, line
        FROM snapshots
        WHERE sport=? AND market=? AND ts_utc>=?
        """,
        con,
        params=(sport, market, t0),
    )
    if df.empty:
        return []

    # Parse times early
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True, errors="coerce")
    df["commence_time_utc"] = pd.to_datetime(df["commence_time_utc"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts_utc", "commence_time_utc", "line"]).copy()

    # Keep only future games
    df = df[df["commence_time_utc"] > pd.Timestamp.utcnow()].copy()
    if df.empty:
        return []

    # Normalize spreads outcomes to HOME/AWAY so confirmations work across books
    if market == "spreads":
        out = df["outcome"].astype(str)
        home = df["home_team"].astype(str)
        away = df["away_team"].astype(str)
        df.loc[out == home, "outcome"] = "HOME"
        df.loc[out == away, "outcome"] = "AWAY"

    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    df = df.dropna(subset=["line"]).copy()
    df = df.sort_values(["event_id", "outcome", "bookmaker_key", "ts_utc"])

    books = config["books"]
    sharp_books = config.get("sharp_books") or [
        books.get("pinnacle", "pinnacle"),
        books.get("betonline", "betonlineag"),
    ]

    move_thr = float(config["move_thresholds"][market])
    plateau_min = int(config.get("plateau_minutes", 3))
    snapback = float(config["snapback_points"][market])
    min_conf = int(config.get("min_confirmations", 0))

    # scoring weights
    w_move = float(config.get("score_w_move", 3.0))
    w_conf = float(config.get("score_w_conf", 1.5))
    w_best = float(config.get("score_w_best", 0.75))

    triggers: List[Trigger] = []

    def better(line_a: float, line_b: float, outcome: str) -> bool:
        # "best line" for the side you are betting
        if market == "spreads":
            return line_a > line_b
        if str(outcome).upper() == "OVER":
            return line_a < line_b
        if str(outcome).upper() == "UNDER":
            return line_a > line_b
        return False

    for (event_id, outcome), g in df.groupby(["event_id", "outcome"], sort=False):
        series = {}
        for b, gb in g.groupby("bookmaker_key"):
            gb = gb.sort_values("ts_utc")
            series[b] = gb[["ts_utc", "line"]].dropna()

        if not any(b in series for b in sharp_books):
            continue

        # Find earliest sharp hit
        origin = None  # (book, t_hit, before, after)
        best_origin_time = None

        for sb in sharp_books:
            if sb not in series:
                continue
            s = series[sb]
            if len(s) < 2:
                continue
            s2 = s.copy()
            s2["dline"] = s2["line"].diff()
            hit = s2[s2["dline"].abs() >= move_thr]
            if hit.empty:
                continue
            t_hit = hit.iloc[0]["ts_utc"]
            if best_origin_time is None or t_hit < best_origin_time:
                idx = hit.index[0]
                pos = s2.index.get_loc(idx)
                if pos == 0:
                    continue
                line_before = float(s2.iloc[pos - 1]["line"])
                line_after = float(s2.iloc[pos]["line"])
                origin = (sb, t_hit, line_before, line_after)
                best_origin_time = t_hit

        if origin is None:
            continue

        origin_book, t_hit, line_before, line_after = origin
        origin_move = float(line_after - line_before)
        direction = 1.0 if origin_move > 0 else -1.0

        # confirmations: other sharp books moved same direction after t_hit
        confirmations = 0
        for other in sharp_books:
            if other == origin_book or other not in series:
                continue
            s = series[other]
            near = s[s["ts_utc"] <= t_hit]
            if near.empty:
                continue
            line_near = float(near.iloc[-1]["line"])
            line_latest = float(s.iloc[-1]["line"])
            if (line_latest - line_near) * direction >= (move_thr * 0.5):
                confirmations += 1

        if confirmations < min_conf:
            continue

        # Plateau check (allow small plateau by default)
        s_origin = series.get(origin_book)
        last_ts = s_origin.iloc[-1]["ts_utc"]
        minutes_since = (pd.Timestamp.utcnow() - last_ts).total_seconds() / 60.0
        if minutes_since < plateau_min:
            continue

        # Snapback check
        after = s_origin[s_origin["ts_utc"] >= t_hit]
        if len(after) >= 2:
            last_line = float(after.iloc[-1]["line"])
            max_line = float(after["line"].max())
            min_line = float(after["line"].min())
            if direction > 0 and (max_line - last_line) >= snapback:
                continue
            if direction < 0 and (last_line - min_line) >= snapback:
                continue

        # Followers: across all books
        followers = 0
        followers_list = []
        for b, s in series.items():
            near = s[s["ts_utc"] <= t_hit]
            if near.empty:
                continue
            line_near = float(near.iloc[-1]["line"])
            line_latest = float(s.iloc[-1]["line"])
            if (line_latest - line_near) * direction >= (move_thr * 0.5):
                followers += 1
                followers_list.append(b)

        # Best line (across bet_books if provided; else across all)
        bet_books = config.get("bet_books") or list(series.keys())
        best_line = None
        best_book = None
        for b in bet_books:
            if b not in series:
                continue
            line_latest = float(series[b].iloc[-1]["line"])
            if best_line is None or better(line_latest, best_line, outcome):
                best_line = line_latest
                best_book = b

        if best_line is None:
            continue

        # scoring (simple + stable)
        steam_strength = abs(origin_move) / move_thr if move_thr > 0 else 0.0
        minutes_to_game = (g["commence_time_utc"].iloc[0] - pd.Timestamp.utcnow()).total_seconds() / 60.0

        score = (w_move * steam_strength) + (w_conf * confirmations) + (w_best * followers)

        notes = f"conf={confirmations} followers={followers}"
        triggers.append(
            Trigger(
                sport=sport,
                event_id=str(event_id),
                commence_time_utc=str(g["commence_time_utc"].iloc[0].to_pydatetime().replace(tzinfo=timezone.utc).isoformat()),
                home_team=str(g["home_team"].iloc[0]),
                away_team=str(g["away_team"].iloc[0]),
                market=market,
                outcome=str(outcome),
                origin_book=str(origin_book),
                origin_move=float(origin_move),
                current_best_line=float(best_line),
                current_best_book=str(best_book),
                ts_utc=_utc_now().isoformat(),
                notes=notes,
                score=float(score),
                minutes_to_game=float(minutes_to_game),
                steam_strength=float(steam_strength),
                sharp_confirmations=int(confirmations),
                followers=int(followers),
                followers_list=",".join(followers_list),
            )
        )

    return triggers

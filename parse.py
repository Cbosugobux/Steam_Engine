from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def parse_odds_payload(payload: Any, sport: str, allowed_book_keys: set[str], markets: set[str]):
    """
    Returns list[dict] rows compatible with db.insert_many schema:
      ts_utc, sport, event_id, commence_time_utc, home_team, away_team,
      bookmaker_key, market, outcome, line, price
    Works for:
      - spreads/totals (team markets)
      - player props (player_* markets)
    """
    ts = _utc_now_iso()
    rows: list[dict] = []

    # payload can be list (sport odds endpoint) or dict (event odds endpoint)
    events = payload if isinstance(payload, list) else [payload]

    for ev in events:
        event_id = str(ev.get("id"))
        commence_time = ev.get("commence_time")
        home_team = ev.get("home_team")
        away_team = ev.get("away_team")

        for bm in ev.get("bookmakers", []) or []:
            bkey = bm.get("key")
            if not bkey or bkey not in allowed_book_keys:
                continue

            for mk in bm.get("markets", []) or []:
                mkey = mk.get("key")
                if not mkey or (markets and mkey not in markets):
                    continue

                for o in mk.get("outcomes", []) or []:
                    name = o.get("name")
                    price = o.get("price")
                    point = o.get("point")

                    # For player props, outcomes often have:
                    # name="Over"/"Under", description="<player name>", point=<line>
                    # For spreads/totals, outcomes often have:
                    # name="<team>" or "Over"/"Under", point=<line>
                    desc = o.get("description")

                    if desc:
                        outcome = f"{desc} {name}".strip()
                    else:
                        outcome = str(name).strip() if name is not None else ""

                    if outcome == "" or point is None:
                        # some markets may not be point-based; we skip those
                        continue

                    try:
                        line = float(point)
                    except Exception:
                        continue

                    try:
                        price_i = int(price) if price is not None else None
                    except Exception:
                        price_i = None

                    rows.append({
                        "ts_utc": ts,
                        "sport": sport,
                        "event_id": event_id,
                        "commence_time_utc": commence_time,
                        "home_team": home_team,
                        "away_team": away_team,
                        "bookmaker_key": bkey,
                        "market": mkey,
                        "outcome": outcome,
                        "line": line,
                        "price": price_i,
                    })

    return rows

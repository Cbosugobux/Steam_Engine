import os
import json
import argparse
import time
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from db import connect, init_db, insert_many, was_alert_sent, mark_alert_sent
from odds_api import OddsApiClient
from parse import parse_odds_payload
from detect import detect_triggers, trigger_key_hash
from notify import send_discord_embed

BASE_DIR = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")


def ts_utc_str() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_et() -> datetime:
    return datetime.now(ET)


def _to_et_str(utc_str: str) -> str:
    try:
        dt = datetime.fromisoformat(str(utc_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_et = dt.astimezone(ET)
        s = dt_et.strftime("%a %b %d • %I:%M %p ET")
        return s.replace(" 0", " ")
    except Exception:
        return str(utc_str)


def load_config() -> dict:
    return json.loads((BASE_DIR / "config.json").read_text())


def cmd_pull(args, cfg):
    api_key = os.getenv("ODDS_API_KEY", "").strip()
    if not api_key or api_key == "PASTE_YOUR_ODDS_API_KEY_HERE":
        raise SystemExit("Missing/invalid ODDS_API_KEY (check /root/steam_engine/.env)")

    client = OddsApiClient(api_key)

    allowed_books = set(cfg["books"].values())
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    bookmakers = ",".join(sorted(allowed_books))

    payload = client.odds(
        sport=args.sport,
        regions=args.regions,
        markets=",".join(markets),
        odds_format="american",
        bookmakers=bookmakers,
    )

    rows = parse_odds_payload(
        payload,
        sport=args.sport,
        allowed_book_keys=allowed_books,
        markets=set(markets),
    )

    con = connect(Path(args.db))
    init_db(con)
    n = insert_many(con, rows)
    print(f"[{ts_utc_str()}] Inserted {n} rows (duplicates ignored). Events returned: {len(payload)}")


def cmd_detect(args, cfg):
    con = connect(Path(args.db))
    init_db(con)

    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    all_tr = []
    for mkt in markets:
        all_tr.extend(detect_triggers(con, sport=args.sport, market=mkt, config=cfg))

    if not all_tr:
        print("No triggers.")
        return

    df = pd.DataFrame([t.__dict__ for t in all_tr]).sort_values(["commence_time_utc", "market", "outcome"])
    out = Path(args.out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print(f"Wrote triggers -> {out} ({len(df)})")
    print(df.head(20).to_string(index=False))


def cmd_run(args, cfg):
    cmd_pull(args, cfg)

    con = connect(Path(args.db))
    init_db(con)

    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    triggers = []
    for mkt in markets:
        triggers.extend(detect_triggers(con, sport=args.sport, market=mkt, config=cfg))

    if not triggers:
        print("No triggers.")
        return

    score_min = float(cfg.get("score_min", 6.0))
    max_alerts = int(cfg.get("max_alerts_per_run", 5))

    start_hour = int(cfg.get("alert_window_start_hour_et", 10))
    start_min = int(cfg.get("alert_window_start_min_et", 0))
    end_hour = int(cfg.get("alert_window_end_hour_et", 22))
    end_min = int(cfg.get("alert_window_end_min_et", 0))

    min_minutes_to_game = float(cfg.get("min_minutes_to_game", 30))
    max_minutes_to_game = float(cfg.get("max_minutes_to_game", 12 * 60))

    now_et = _now_et()
    in_window = ((now_et.hour, now_et.minute) >= (start_hour, start_min)) and ((now_et.hour, now_et.minute) <= (end_hour, end_min))
    if not in_window:
        print(f"Outside alert window ET ({start_hour:02d}:{start_min:02d}-{end_hour:02d}:{end_min:02d}).")
        return

    # filter
    filt = []
    for t in triggers:
        sc = float(getattr(t, "score", 0.0))
        if sc < score_min:
            continue
        mtg = float(getattr(t, "minutes_to_game", 999999))
        if mtg < min_minutes_to_game or mtg > max_minutes_to_game:
            continue
        filt.append(t)

    if not filt:
        print("No triggers after filters.")
        return

    # kill duality: ONE per (event_id, market)
    best = {}
    for t in filt:
        k = (str(t.event_id), str(t.market))
        if k not in best or float(getattr(t, "score", 0.0)) > float(getattr(best[k], "score", 0.0)):
            best[k] = t

    picks = list(best.values())
    picks.sort(key=lambda x: float(getattr(x, "score", 0.0)), reverse=True)
    picks = picks[:max_alerts]

    alerts = 0
    skipped = 0
    failed = 0

    for t in picks:
        key = trigger_key_hash(t)
        if was_alert_sent(con, key):
            skipped += 1
            continue

        sc = float(getattr(t, "score", 0.0))
        start_et = _to_et_str(t.commence_time_utc)

        title = f"🏀 {t.sport.upper()} — {t.market.upper()} {t.outcome} — SCORE {sc:.2f}"
        desc = (
            f"**{t.away_team} @ {t.home_team}**\n"
            f"Start: {start_et}\n"
            f"Minutes to game: {float(getattr(t,'minutes_to_game',0.0)):.0f}"
        )
        fields = [
            {"name": "Origin", "value": f"{t.origin_book} ({t.origin_move:+.2f})", "inline": True},
            {"name": "Best Now", "value": f"{t.current_best_book} {float(t.current_best_line):g}", "inline": True},
            {"name": "Confirmations", "value": str(getattr(t, "sharp_confirmations", 0)), "inline": True},
            {"name": "Followers", "value": f'{getattr(t,"followers",0)} | {str(getattr(t,"followers_list",""))[:120]}', "inline": False},
            {"name": "Notes", "value": str(getattr(t, "notes", ""))[:400], "inline": False},
        ]

        ok = send_discord_embed(title, desc, fields)
        if not ok:
            failed += 1
            print(f"DISCORD FAILED: {t.away_team} @ {t.home_team} | {t.market} {t.outcome}")
            continue

        mark_alert_sent(con, t.ts_utc, t.sport, str(t.event_id), t.market, t.outcome, key)

        # paper bet entry for CLV tracking
        cur = con.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO paper_bets
            (ts_utc, sport, event_id, market, outcome, commence_time_utc,
             entry_book, entry_line, entry_price, alert_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t.ts_utc, t.sport, str(t.event_id), t.market, t.outcome, t.commence_time_utc,
                str(t.current_best_book), float(t.current_best_line),
                getattr(t, "current_best_price", None),
                key
            )
        )
        con.commit()

        alerts += 1
        time.sleep(0.35)

    print(f"Top picks: {len(picks)} | Alerts sent: {alerts} | Skipped: {skipped} | Failed: {failed}")


def cmd_close(args, cfg):
    close_window = int(cfg.get("close_window_minutes", 15))

    con = connect(Path(args.db))
    init_db(con)
    cur = con.cursor()

    cur.execute("""
      SELECT bet_id, sport, event_id, market, outcome, commence_time_utc
      FROM paper_bets
      WHERE bet_id NOT IN (SELECT bet_id FROM paper_closes)
    """)
    bets = cur.fetchall()
    if not bets:
        print("No open paper bets.")
        return

    now = datetime.now(timezone.utc)
    closed = 0

    for bet_id, sport, event_id, market, outcome, commence_time_utc in bets:
        try:
            start = datetime.fromisoformat(str(commence_time_utc).replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        minutes_to = (start - now).total_seconds() / 60.0
        if minutes_to > close_window:
            continue
        if minutes_to < -120:
            continue

        cur.execute("""
          SELECT bookmaker_key, line, price, ts_utc
          FROM snapshots
          WHERE sport=? AND event_id=? AND market=? AND outcome=?
          ORDER BY (bookmaker_key='pinnacle') DESC, ts_utc DESC
          LIMIT 1
        """, (sport, str(event_id), market, outcome))
        r = cur.fetchone()
        if not r:
            continue

        close_book, close_line, close_price, ts_close = r

        cur.execute("""
          INSERT OR REPLACE INTO paper_closes
          (bet_id, ts_close_utc, close_book, close_line, close_price)
          VALUES (?, ?, ?, ?, ?)
        """, (bet_id, ts_close, close_book, float(close_line), close_price))
        con.commit()
        closed += 1

    print(f"Closed bets recorded: {closed}")


def cmd_clv(args, cfg):
    con = connect(Path(args.db))
    init_db(con)

    q = """
    SELECT
      b.bet_id,
      b.ts_utc AS ts_entry_utc,
      b.sport,
      b.event_id,
      b.market,
      b.outcome,
      b.commence_time_utc,
      b.entry_book,
      b.entry_line,
      c.ts_close_utc,
      c.close_book,
      c.close_line,
      (c.close_line - b.entry_line) AS clv
    FROM paper_bets b
    JOIN paper_closes c ON c.bet_id = b.bet_id
    ORDER BY b.ts_utc DESC
    """
    df = pd.read_sql_query(q, con)

    out = Path(args.out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print(f"Wrote CLV report -> {out} rows={len(df)}")
    if len(df):
        print(df.head(25).to_string(index=False))


def main():
    # IMPORTANT: override=True fixes your "still reading PASTE_YOUR..." problem
    load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)
    cfg = load_config()

    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/odds.db")
    p.add_argument("--sport", required=True)
    p.add_argument("--markets", default="spreads,totals")
    p.add_argument("--regions", default="us,us2,eu")
    p.add_argument("--out_csv", default="data/triggers.csv")

    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pull")
    sub.add_parser("detect")
    sub.add_parser("run")
    sub.add_parser("close")
    sub.add_parser("clv")

    args = p.parse_args()

    if args.cmd == "pull":
        cmd_pull(args, cfg)
    elif args.cmd == "detect":
        cmd_detect(args, cfg)
    elif args.cmd == "run":
        cmd_run(args, cfg)
    elif args.cmd == "close":
        cmd_close(args, cfg)
    elif args.cmd == "clv":
        cmd_clv(args, cfg)


if __name__ == "__main__":
    main()

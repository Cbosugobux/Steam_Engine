import sqlite3
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from notify import send_discord_embed

DB = "/root/steam_engine/data/odds.db"

def main():
    load_dotenv(dotenv_path="/root/steam_engine/.env")

    con = sqlite3.connect(DB)
    cur = con.cursor()

    # last 24h entries
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    q = """
    SELECT
      b.sport,
      b.market,
      COUNT(*) as n,
      AVG(c.close_line - b.entry_line) as avg_clv,
      MIN(c.close_line - b.entry_line) as min_clv,
      MAX(c.close_line - b.entry_line) as max_clv
    FROM paper_bets b
    JOIN paper_closes c ON c.bet_id = b.bet_id
    WHERE b.ts_utc >= ?
    GROUP BY b.sport, b.market
    ORDER BY n DESC
    """
    rows = cur.execute(q, (since,)).fetchall()

    # totals
    q2 = """
    SELECT
      COUNT(*) as n,
      AVG(c.close_line - b.entry_line) as avg_clv
    FROM paper_bets b
    JOIN paper_closes c ON c.bet_id = b.bet_id
    WHERE b.ts_utc >= ?
    """
    n_all, avg_all = cur.execute(q2, (since,)).fetchone()
    con.close()

    title = "📈 CLV Report (last 24h)"
    desc = f"Closed paper bets: **{n_all or 0}**\nAvg CLV: **{(avg_all or 0):+.3f}**"

    fields = []
    for sport, market, n, avg_clv, min_clv, max_clv in rows[:12]:
        fields.append({
            "name": f"{sport} — {market}",
            "value": f"n={n} | avg={avg_clv:+.3f} | min={min_clv:+.3f} | max={max_clv:+.3f}",
            "inline": False
        })

    if not fields:
        fields = [{"name":"No data yet", "value":"No closed paper bets in the last 24h (run close on schedule).", "inline": False}]

    send_discord_embed(title, desc, fields)

if __name__ == "__main__":
    main()

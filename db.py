import sqlite3
from pathlib import Path

def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path))

def init_db(con):
    cur = con.cursor()


    # One active alert per (sport,event_id,market). We "upgrade" if a stronger signal appears later.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS alert_positions (
      sport TEXT NOT NULL,
      event_id TEXT NOT NULL,
      market TEXT NOT NULL,
      outcome TEXT NOT NULL,
      best_score REAL NOT NULL,
      alert_key TEXT NOT NULL,
      last_ts_utc TEXT NOT NULL,
      PRIMARY KEY (sport, event_id, market)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS snapshots (
        ts_utc TEXT,
        sport TEXT,
        event_id TEXT,
        commence_time_utc TEXT,
        home_team TEXT,
        away_team TEXT,
        bookmaker_key TEXT,
        market TEXT,
        outcome TEXT,
        line REAL,
        price REAL,
        PRIMARY KEY (ts_utc, sport, event_id, bookmaker_key, market, outcome)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS alerts_sent (
        alert_key TEXT PRIMARY KEY,
        ts_utc TEXT,
        sport TEXT,
        event_id TEXT,
        market TEXT,
        outcome TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_bets (
        bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT,
        sport TEXT,
        event_id TEXT,
        market TEXT,
        outcome TEXT,
        commence_time_utc TEXT,
        entry_book TEXT,
        entry_line REAL,
        entry_price REAL,
        alert_key TEXT UNIQUE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_closes (
        bet_id INTEGER PRIMARY KEY,
        ts_close_utc TEXT,
        close_book TEXT,
        close_line REAL,
        close_price REAL
    )
    """)

    con.commit()

def insert_many(con, rows):
    if not rows:
        return 0
    cur = con.cursor()
    cur.executemany(
        """
        INSERT OR IGNORE INTO snapshots
        (ts_utc, sport, event_id, commence_time_utc, home_team, away_team,
         bookmaker_key, market, outcome, line, price)
        VALUES
        (:ts_utc, :sport, :event_id, :commence_time_utc, :home_team, :away_team,
         :bookmaker_key, :market, :outcome, :line, :price)
        """,
        rows
    )
    con.commit()
    return cur.rowcount

def was_alert_sent(con, key: str) -> bool:
    cur = con.cursor()
    cur.execute("SELECT 1 FROM alerts_sent WHERE alert_key=?", (key,))
    return cur.fetchone() is not None

def mark_alert_sent(con, ts_utc, sport, event_id, market, outcome, key):
    cur = con.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO alerts_sent
        (alert_key, ts_utc, sport, event_id, market, outcome)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (key, ts_utc, sport, event_id, market, outcome)
    )
    con.commit()

def _ensure_indexes(con):
    cur = con.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_alert_positions_score ON alert_positions(best_score)")
    con.commit()

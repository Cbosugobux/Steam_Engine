# Sharp-Origin Steam Engine (NBA + NCAAB) — Odds API + SMS Alerts

This project:
- Pulls odds snapshots from The Odds API v4 for NBA + NCAAB spreads/totals
- Tracks key books: Pinnacle + BetOnline + US books (DK/FD/MGM/Hard Rock)
- Detects "sharp-origin" moves (Pinnacle/BetOnline lead) + confirmations + plateau/no-snapback
- Writes everything to SQLite (portable)
- Sends SMS alerts via Twilio when a candidate triggers

## Setup

### 1) Create a virtualenv
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt

### 2) Environment variables
Copy `.env.example` to `.env` and fill in:
- ODDS_API_KEY
- (Optional) TWILIO_* and TO_NUMBER

### 3) Run (one-shot)
python main.py run --sport basketball_nba --markets spreads,totals

For NCAAB:
python main.py run --sport basketball_ncaab --markets spreads,totals

## Cron (DigitalOcean Ubuntu)

Every 10 minutes:
*/10 * * * * cd /path/to/steam_engine && /path/to/.venv/bin/python main.py run --sport basketball_nba --markets spreads,totals >> logs/nba.log 2>&1

Tip: add a time window in cron to avoid overnight junk pulls.

## Notes
- Odds API v4 docs: https://the-odds-api.com/liveapi/guides/v4/
- Rate limit is 30 requests/sec; this client retries 429 with backoff.
- Player props are expensive (event-level); this project focuses on spreads/totals first.

import os
import time
import json
import requests
from dotenv import load_dotenv

def send_discord_embed(title: str, description: str, fields: list[dict]) -> bool:
    # Load env every call (safe for cron) and override stale vars
    load_dotenv("/root/steam_engine/.env", override=True)

    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print("DISCORD_WEBHOOK_URL missing")
        return False

    payload = {
        "embeds": [{
            "title": title[:256],
            "description": description[:4096],
            "fields": fields[:25],
        }]
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "steam_engine/1.0 (+https://discord.com)",
    }

    # try twice if rate limited
    for attempt in range(2):
        try:
            r = requests.post(url, data=json.dumps(payload), headers=headers, timeout=20)
            if r.status_code == 204:
                return True

            if r.status_code == 429:
                try:
                    data = r.json()
                    sleep_s = float(data.get("retry_after", 0.5))
                except Exception:
                    sleep_s = 0.5
                time.sleep(min(max(sleep_s, 0.25), 2.0))
                continue

            print(f"Discord status: {r.status_code} {r.text[:250]}")
            return False
        except Exception as e:
            print("Discord exception:", repr(e))
            time.sleep(0.5)

    return False

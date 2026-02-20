from __future__ import annotations

from typing import Any, Dict, List, Optional
import requests


class OddsApiClient:
    def __init__(self, api_key: str, base_url: str = "https://api.the-odds-api.com/v4", timeout: int = 30):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if params is None:
            params = {}
        params = dict(params)
        params["apiKey"] = self.api_key

        url = f"{self.base_url}{path}"
        r = requests.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # Standard odds endpoint (works for spreads/totals/etc)
    def odds(self, sport: str, regions: str, markets: str, odds_format: str, bookmakers: str) -> List[Dict[str, Any]]:
        return self._get(
            f"/sports/{sport}/odds",
            {
                "regions": regions,
                "markets": markets,
                "oddsFormat": odds_format,
                "bookmakers": bookmakers,
            },
        )

    # Events list endpoint (needed for player props)
    def events(self, sport: str) -> List[Dict[str, Any]]:
        return self._get(f"/sports/{sport}/events")

    # Event-specific odds endpoint (needed for player props markets)
    def event_odds(
        self,
        sport: str,
        event_id: str,
        regions: str,
        markets: str,
        odds_format: str,
        bookmakers: str,
    ) -> Any:
        return self._get(
            f"/sports/{sport}/events/{event_id}/odds",
            {
                "regions": regions,
                "markets": markets,
                "oddsFormat": odds_format,
                "bookmakers": bookmakers,
            },
        )

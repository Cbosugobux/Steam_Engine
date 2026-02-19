import requests

class OddsApiClient:
    def __init__(self, api_key: str, base_url: str = "https://api.the-odds-api.com/v4"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def odds(self, sport: str, regions: str, markets: str, odds_format: str = "american", bookmakers: str | None = None):
        url = f"{self.base_url}/sports/{sport}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": odds_format,
        }
        if bookmakers:
            params["bookmakers"] = bookmakers
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def event_odds(
        self,
        sport: str,
        event_id: str,
        markets: str,
        odds_format: str = "american",
        bookmakers: str | None = None,
        regions: str | None = None,
    ):
        """
        Player props and other additional markets are typically accessed via:
          /sports/{sport}/events/{eventId}/odds
        """
        url = f"{self.base_url}/sports/{sport}/events/{event_id}/odds"
        params = {
            "apiKey": self.api_key,
            "markets": markets,
            "oddsFormat": odds_format,
        }
        # Use bookmakers to avoid region multipliers; perfect for Pinnacle-only pulls
        if bookmakers:
            params["bookmakers"] = bookmakers
        elif regions:
            params["regions"] = regions

        r = requests.get(url, params=params, timeout=25)
        r.raise_for_status()
        return r.json()

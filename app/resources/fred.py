"""FRED resource: St. Louis Fed economic data series search."""

from __future__ import annotations

import httpx

from app.resources.base import Resource

FRED_SERIES_SEARCH = "https://api.stlouisfed.org/fred/series/search"


class FredResource(Resource):
    name = "fred"
    timeout_s = 10.0

    def __init__(self, client: httpx.AsyncClient, api_key: str):
        super().__init__(client)
        self.api_key = api_key

    async def search(self, params: dict) -> list[dict]:
        query = {
            "search_text": params["query"],
            "api_key": self.api_key,
            "file_type": "json",
            "limit": params.get("limit", 10),
        }
        data = await self._get_json(FRED_SERIES_SEARCH, params=query)
        return [
            {
                "title": s.get("title"),
                "url": f"https://fred.stlouisfed.org/series/{s.get('id')}",
                "date": s.get("last_updated"),
                "snippet": f"{s.get('frequency')} · {s.get('units')}",
                "source": "fred",
            }
            for s in data.get("seriess", [])
        ]

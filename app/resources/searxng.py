"""SearXNG resource. One instance per SearXNG category (general, news)."""

from __future__ import annotations

import httpx

from app.resources.base import Resource


def _time_range(days: int | None) -> str | None:
    if days is None:
        return None
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    return "year"


class SearxngResource(Resource):
    def __init__(self, client: httpx.AsyncClient, base_url: str, category: str, name: str):
        super().__init__(client)
        self.base_url = base_url.rstrip("/")
        self.category = category
        self.name = name

    async def search(self, params: dict) -> list[dict]:
        query = {"q": params["query"], "format": "json", "categories": self.category}
        time_range = _time_range(params.get("days"))
        if time_range:
            query["time_range"] = time_range

        data = await self._get_json(f"{self.base_url}/search", params=query)
        limit = params.get("limit", 10)
        return [
            {
                "title": r.get("title"),
                "url": r.get("url"),
                "date": r.get("publishedDate"),
                "snippet": r.get("content"),
                "source": r.get("engine"),
            }
            for r in data.get("results", [])[:limit]
        ]

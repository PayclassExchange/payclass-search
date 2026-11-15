"""CourtListener resource: free RECAP archive of federal dockets, including bankruptcy."""

from __future__ import annotations

import httpx

from app.resources.base import Resource

COURTLISTENER_SEARCH = "https://www.courtlistener.com/api/rest/v4/search/"


class CourtListenerResource(Resource):
    name = "courtlistener"
    timeout_s = 15.0

    def __init__(self, client: httpx.AsyncClient, token: str | None):
        super().__init__(client)
        self.token = token

    async def search(self, params: dict) -> list[dict]:
        query = {"q": f'"{params["party_name"]}"', "type": "r"}
        if params.get("court"):
            query["court"] = params["court"]
        headers = {"Authorization": f"Token {self.token}"} if self.token else None

        data = await self._get_json(COURTLISTENER_SEARCH, params=query, headers=headers)
        limit = params.get("limit", 10)
        items = []
        for r in data.get("results", [])[:limit]:
            path = r.get("absolute_url") or ""
            items.append(
                {
                    "title": r.get("caseName"),
                    "url": f"https://www.courtlistener.com{path}" if path else None,
                    "date": r.get("dateFiled"),
                    "snippet": f"Docket {r.get('docketNumber')} · {r.get('court')}",
                    "source": "courtlistener",
                }
            )
        return items

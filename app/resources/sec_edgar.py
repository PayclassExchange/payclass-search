"""SEC EDGAR full-text search: public filings and Form D private offerings."""

from __future__ import annotations

import httpx

from app.resources.base import Resource

EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"


class SecEdgarResource(Resource):
    name = "sec_edgar"
    timeout_s = 15.0

    def __init__(self, client: httpx.AsyncClient, user_agent: str):
        super().__init__(client)
        self.user_agent = user_agent

    async def search(self, params: dict) -> list[dict]:
        query = {"q": f'"{params["query"]}"'}
        if params.get("forms"):
            query["forms"] = ",".join(params["forms"])

        data = await self._get_json(
            EDGAR_SEARCH,
            params=query,
            headers={"User-Agent": self.user_agent},
        )
        limit = params.get("limit", 10)
        items = []
        for hit in data.get("hits", {}).get("hits", [])[:limit]:
            src = hit.get("_source", {})
            names = ", ".join(src.get("display_names", []))
            ciks = src.get("ciks", [])
            accession = src.get("adsh", "")
            url = None
            if ciks and accession:
                url = (
                    f"https://www.sec.gov/Archives/edgar/data/{int(ciks[0])}/"
                    f"{accession.replace('-', '')}/{accession}-index.htm"
                )
            items.append(
                {
                    "title": f"{src.get('form')} · {names}",
                    "url": url,
                    "date": src.get("file_date"),
                    "snippet": None,
                    "source": "sec_edgar",
                }
            )
        return items

"""Searches the GDELT DOC 2.0 global news index. Public API, no key needed."""

from __future__ import annotations

import httpx

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class GdeltError(Exception):
    pass


async def gdelt_news(client: httpx.AsyncClient, query: str, limit: int) -> list[dict]:
    params = {
        "query": f'"{query}"',
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(limit),
        "timespan": "3months",
        "sort": "DateDesc",
    }
    try:
        resp = await client.get(GDELT_DOC_URL, params=params, timeout=20.0)
    except httpx.TransportError as exc:
        raise GdeltError("GDELT unreachable") from exc
    if resp.status_code >= 400:
        raise GdeltError(f"GDELT returned {resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        return []
    articles = data.get("articles") if isinstance(data, dict) else None
    if not isinstance(articles, list):
        return []

    items = []
    for article in articles:
        if not isinstance(article, dict) or not article.get("url"):
            continue
        meta = " · ".join(part for part in (article.get("domain"), article.get("seendate")) if part)
        items.append({
            "title": article.get("title") or article["url"],
            "url": article["url"],
            "snippet": meta,
        })
    return items

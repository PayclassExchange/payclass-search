"""Searches the GDELT DOC 2.0 global news index. Public API, no key needed."""

from __future__ import annotations

import asyncio
import time

import httpx

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# Cap: 10 GDELT requests per minute, one every 6 seconds. GDELT returns 429 when it is pushed harder.
MIN_GAP_S = 6.0

_lock = asyncio.Lock()
_last_call = 0.0


class GdeltError(Exception):
    pass


async def _throttled_get(client: httpx.AsyncClient, params: dict) -> httpx.Response:
    global _last_call
    async with _lock:
        wait = MIN_GAP_S - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        resp = await client.get(GDELT_DOC_URL, params=params, timeout=20.0)
        _last_call = time.monotonic()
        if resp.status_code == 429:
            await asyncio.sleep(MIN_GAP_S)
            resp = await client.get(GDELT_DOC_URL, params=params, timeout=20.0)
            _last_call = time.monotonic()
        return resp


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
        resp = await _throttled_get(client, params)
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

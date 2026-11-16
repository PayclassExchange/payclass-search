"""Keeps and orders one source's results by how likely each one is about the searched entity.

Ranking runs in n8n (workflow "Search Rank"), which calls the LiteLLM gateway.
This service never calls LiteLLM directly. The n8n webhook returns the indices of
the results that are about the entity, most relevant first. Indices it leaves out
are dropped. If ranking is not configured or fails, every result stays in its
original order.
"""

from __future__ import annotations

import httpx

from app.config import Settings

RANK_HEADER = "X-Search-Key"


def _identity(n: int) -> list[int]:
    return list(range(n))


def _valid_order(order: object, n: int) -> list[int] | None:
    if not isinstance(order, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in order):
        return None
    if len(set(order)) != len(order) or not all(0 <= i < n for i in order):
        return None
    return order


async def rank_items(client: httpx.AsyncClient, settings: Settings, query: str, items: list[dict]) -> list[int]:
    n = len(items)
    if n == 0:
        return []
    if not (settings.n8n_rank_url and settings.api_key):
        return _identity(n)

    body = {
        "query": query,
        "items": [
            {"title": it.get("title", ""), "snippet": it.get("snippet", ""), "url": it.get("url", "")}
            for it in items
        ],
    }
    try:
        resp = await client.post(
            settings.n8n_rank_url,
            json=body,
            headers={RANK_HEADER: settings.api_key},
            timeout=30.0,
        )
    except httpx.TransportError:
        return _identity(n)
    if resp.status_code >= 400:
        return _identity(n)

    try:
        order = _valid_order(resp.json()["order"], n)
    except (KeyError, TypeError, ValueError):
        return _identity(n)
    return order if order is not None else _identity(n)

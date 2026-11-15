"""Base class for search resources. Each resource is one external source."""

from __future__ import annotations

from typing import Any

import httpx


class ResourceError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class Resource:
    name: str = "resource"
    timeout_s: float = 10.0

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def search(self, params: dict) -> list[dict]:
        raise NotImplementedError

    async def _get_json(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> Any:
        # Never put the URL or query into error messages: some URLs carry API keys.
        try:
            resp = await self.client.get(url, params=params, headers=headers, timeout=self.timeout_s)
        except httpx.TransportError as exc:
            raise ResourceError(f"{self.name}: network error", retryable=True) from exc

        if resp.status_code == 429:
            raise ResourceError(f"{self.name}: rate limited")
        if resp.status_code >= 500:
            raise ResourceError(f"{self.name}: upstream {resp.status_code}", retryable=True)
        if resp.status_code >= 400:
            raise ResourceError(f"{self.name}: upstream {resp.status_code}")

        try:
            return resp.json()
        except ValueError as exc:
            raise ResourceError(f"{self.name}: invalid JSON") from exc

"""Result cache. Uses shared Redis when REDIS_URL is set, otherwise memory."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger("search_manager.cache")


def make_key(resource: str, params: dict) -> str:
    raw = json.dumps([resource, params], sort_keys=True, default=str)
    return "sm:" + hashlib.sha256(raw.encode()).hexdigest()


class Cache:
    def __init__(self, redis_url: str | None, ttl_s: int):
        self.redis_url = redis_url
        self.ttl_s = ttl_s
        self._redis = None
        self._local: dict[str, tuple[float, Any]] = {}

    async def connect(self) -> None:
        if self.redis_url:
            import redis.asyncio as redis

            self._redis = redis.from_url(self.redis_url, decode_responses=True)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    async def get(self, key: str) -> Any | None:
        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
                return json.loads(raw) if raw else None
            except Exception:
                logger.warning("redis get failed; treating as cache miss")
                return None

        entry = self._local.get(key)
        if entry and entry[0] > time.monotonic():
            return entry[1]
        self._local.pop(key, None)
        return None

    async def set(self, key: str, value: Any) -> None:
        if self._redis is not None:
            try:
                await self._redis.set(key, json.dumps(value), ex=self.ttl_s)
            except Exception:
                logger.warning("redis set failed; result not cached")
            return

        self._local[key] = (time.monotonic() + self.ttl_s, value)

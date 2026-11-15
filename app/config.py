"""Configuration, read only from environment variables set in Coolify."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _get(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    searxng_url: str | None
    courtlistener_token: str | None
    sec_user_agent: str | None
    fred_api_key: str | None
    litellm_url: str | None
    litellm_api_key: str | None
    intake_model: str | None
    redis_url: str | None
    default_deadline_s: float
    max_deadline_s: float
    resource_concurrency: int
    cache_ttl_s: int
    breaker_threshold: int
    breaker_cooldown_s: float


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        api_key=_get("SEARCH_MANAGER_API_KEY"),
        searxng_url=_get("SEARXNG_URL"),
        courtlistener_token=_get("COURTLISTENER_TOKEN"),
        sec_user_agent=_get("SEC_USER_AGENT"),
        fred_api_key=_get("FRED_API_KEY"),
        litellm_url=_get("LITELLM_URL"),
        litellm_api_key=_get("LITELLM_API_KEY"),
        intake_model=_get("INTAKE_MODEL"),
        redis_url=_get("REDIS_URL"),
        default_deadline_s=float(os.environ.get("DEFAULT_DEADLINE_SECONDS", "15")),
        max_deadline_s=float(os.environ.get("MAX_DEADLINE_SECONDS", "60")),
        resource_concurrency=int(os.environ.get("RESOURCE_CONCURRENCY", "4")),
        cache_ttl_s=int(os.environ.get("CACHE_TTL_SECONDS", "900")),
        breaker_threshold=int(os.environ.get("BREAKER_THRESHOLD", "3")),
        breaker_cooldown_s=float(os.environ.get("BREAKER_COOLDOWN_SECONDS", "60")),
    )

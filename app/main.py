"""Payclass Search: one endpoint that takes a task and returns results."""

from __future__ import annotations

import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from app.cache import Cache
from app.config import Settings, get_settings
from app.executor import CircuitBreaker, Executor
from app.intake import IntakeError, NeedsClarification, plain_language_to_task
from app.models import task_adapter
from app.planner import plan
from app.resources.base import Resource
from app.resources.courtlistener import CourtListenerResource
from app.resources.fred import FredResource
from app.resources.searxng import SearxngResource
from app.resources.sec_edgar import SecEdgarResource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("search_manager")


class TaskRequest(BaseModel):
    task: dict | None = None
    request: str | None = Field(default=None, max_length=1000)
    deadline_seconds: float | None = Field(default=None, gt=0)


def build_resources(settings: Settings, client: httpx.AsyncClient) -> dict[str, Resource]:
    resources: dict[str, Resource] = {}
    if settings.searxng_url:
        resources["searxng_general"] = SearxngResource(
            client, settings.searxng_url, "general", "searxng_general"
        )
        resources["searxng_news"] = SearxngResource(
            client, settings.searxng_url, "news", "searxng_news"
        )
    if settings.courtlistener_token:
        resources["courtlistener"] = CourtListenerResource(client, settings.courtlistener_token)
    if settings.sec_user_agent:
        resources["sec_edgar"] = SecEdgarResource(client, settings.sec_user_agent)
    if settings.fred_api_key:
        resources["fred"] = FredResource(client, settings.fred_api_key)
    return resources


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.api_key:
        raise RuntimeError("SEARCH_MANAGER_API_KEY is not set")

    client = httpx.AsyncClient(follow_redirects=False)
    cache = Cache(settings.redis_url, settings.cache_ttl_s)
    await cache.connect()
    app.state.client = client
    app.state.cache = cache
    app.state.executor = Executor(
        build_resources(settings, client),
        cache,
        CircuitBreaker(settings.breaker_threshold, settings.breaker_cooldown_s),
        settings.resource_concurrency,
    )
    yield
    await client.aclose()
    await cache.close()


app = FastAPI(title="Payclass Search", lifespan=lifespan)


def check_key(authorization: str | None) -> None:
    expected = f"Bearer {get_settings().api_key}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/v1/tasks")
async def run_task(
    body: TaskRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    check_key(authorization)
    settings = get_settings()

    if (body.task is None) == (body.request is None):
        raise HTTPException(status_code=422, detail="send exactly one of 'task' or 'request'")

    try:
        if body.task is not None:
            task = task_adapter.validate_python(body.task)
        else:
            task = await plain_language_to_task(request.app.state.client, settings, body.request)
    except ValidationError:
        raise HTTPException(status_code=422, detail="task does not match a known task type")
    except NeedsClarification as exc:
        return {"status": "clarification_needed", "question": exc.question}
    except IntakeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    deadline = min(body.deadline_seconds or settings.default_deadline_s, settings.max_deadline_s)
    steps = plan(task)
    task_id = uuid.uuid4().hex
    # Log the task type and step count only. Never log query text.
    logger.info("task=%s type=%s steps=%d", task_id, task.type, len(steps))

    results = await request.app.state.executor.run(steps, deadline)
    statuses = {r.status for r in results.values()}
    if statuses == {"ok"}:
        overall = "ok"
    elif "ok" in statuses:
        overall = "partial"
    else:
        overall = "error"

    return {
        "task_id": task_id,
        "status": overall,
        "results": {step_id: asdict(r) for step_id, r in results.items()},
    }

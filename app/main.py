"""Payclass Search: one endpoint that takes a task and returns results."""

from __future__ import annotations

import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict

import httpx
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from app.cache import Cache
from app.config import Settings, get_settings
from app import drive
from app.gdelt import GdeltError, gdelt_news
from app.executor import CircuitBreaker, Executor
from app.intake import IntakeError, NeedsClarification, plain_language_to_task
from app.models import task_adapter
from app.planner import plan
from app.rank import rank_items
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


app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/", include_in_schema=False)
async def ui() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.post("/ui/tasks", include_in_schema=False)
async def ui_run_task(body: TaskRequest, request: Request) -> dict:
    # Same-origin endpoint for the browser page. The server attaches the API key,
    # so the key never reaches the page. Gate this route before sharing the URL.
    return await run_task(body, request, authorization=f"Bearer {get_settings().api_key}")


def _drive_settings() -> tuple[str, str, str]:
    cfg = get_settings()
    if not (cfg.google_oauth_client_id and cfg.google_oauth_client_secret and cfg.session_secret):
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    return cfg.google_oauth_client_id, cfg.google_oauth_client_secret, cfg.session_secret


def _session_email(request: Request) -> dict | None:
    cfg = get_settings()
    if not cfg.session_secret:
        return None
    return drive.unsign(request.cookies.get(drive.SESSION_COOKIE), cfg.session_secret)


@app.get("/auth/google/login", include_in_schema=False)
async def google_login() -> RedirectResponse:
    client_id, _, secret = _drive_settings()
    state = uuid.uuid4().hex
    redirect_uri = get_settings().public_base_url + "/auth/google/callback"
    resp = RedirectResponse(drive.login_url(client_id, redirect_uri, state), status_code=302)
    resp.set_cookie(drive.STATE_COOKIE, drive.sign({"s": state}, secret), max_age=600, httponly=True, secure=True, samesite="lax")
    return resp


@app.get("/auth/google/callback", include_in_schema=False)
async def google_callback(request: Request, code: str, state: str) -> RedirectResponse:
    client_id, client_secret, secret = _drive_settings()
    saved = drive.unsign(request.cookies.get(drive.STATE_COOKIE), secret)
    if not saved or saved.get("s") != state:
        raise HTTPException(status_code=400, detail="Sign-in expired. Try again.")
    redirect_uri = get_settings().public_base_url + "/auth/google/callback"
    client = request.app.state.client
    tokens = await drive.exchange_code(client, client_id, client_secret, code, redirect_uri)
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise HTTPException(status_code=400, detail="Google did not return a refresh token. Try again.")
    email = await drive.email_for_token(client, tokens["access_token"])
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(drive.SESSION_COOKIE, drive.sign({"rt": refresh, "email": email}, secret), httponly=True, secure=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    resp.delete_cookie(drive.STATE_COOKIE)
    return resp


@app.get("/auth/me", include_in_schema=False)
async def auth_me(request: Request) -> dict:
    session = _session_email(request)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    return {"email": session["email"]}


@app.post("/auth/logout", include_in_schema=False)
async def auth_logout() -> RedirectResponse:
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(drive.SESSION_COOKIE)
    return resp


class DriveRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)


@app.post("/ui/drive", include_in_schema=False)
async def ui_drive(body: DriveRequest, request: Request) -> dict:
    client_id, client_secret, _ = _drive_settings()
    session = _session_email(request)
    if not session:
        raise HTTPException(status_code=401, detail="Sign in with Google first")
    client = request.app.state.client
    access = await drive.access_token_for(client, client_id, client_secret, session["rt"])
    items = await drive.drive_search(client, access, body.query, body.limit)
    return {"task_id": uuid.uuid4().hex, "status": "ok", "results": {"drive": {"status": "ok", "items": items}}}


class RankItem(BaseModel):
    title: str = Field(default="", max_length=500)
    snippet: str = Field(default="", max_length=1000)
    url: str = Field(default="", max_length=2000)


class RankRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    items: list[RankItem] = Field(max_length=50)


class GdeltRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=50, ge=1, le=250)


@app.post("/ui/gdelt", include_in_schema=False)
async def ui_gdelt(body: GdeltRequest, request: Request) -> dict:
    try:
        items = await gdelt_news(request.app.state.client, body.query, body.limit)
    except GdeltError as exc:
        return {"status": "error", "items": [], "detail": str(exc)}
    return {"status": "ok", "items": items}


@app.post("/ui/rank", include_in_schema=False)
async def ui_rank(body: RankRequest, request: Request) -> dict:
    settings = get_settings()
    order = await rank_items(
        request.app.state.client,
        settings,
        body.query,
        [item.model_dump() for item in body.items],
    )
    return {"order": order}


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


def _search_query(task) -> str:
    # The text the search steps used, so the page can run Drive and GDELT with the same words.
    if task.type == "entity_background":
        return task.name
    if task.type == "court_search":
        return task.party_name
    return task.query


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
        "search_query": _search_query(task),
        "results": {step_id: asdict(r) for step_id, r in results.items()},
    }

# Payclass Search

A search manager for Payclass. Callers send a task, and the manager decides which
resources to run, in what order, with what timeouts, and returns one result.

## Endpoints

- `GET /healthz`: no key needed.
- `POST /v1/tasks`: requires `Authorization: Bearer <SEARCH_MANAGER_API_KEY>`.

Send either a structured task or a plain-language request (not both):

```json
{"task": {"type": "entity_background", "name": "Acme Corp"}, "deadline_seconds": 15}
```

```json
{"request": "bankruptcy news about John Main"}
```

Plain-language requests need `N8N_INTAKE_WEBHOOK_URL` (the n8n `search-intake` webhook).
n8n calls the LiteLLM Operations model, so this service never talks to LiteLLM. The
model only fills task fields. It never picks resources.

## Task types

| Type | Resources | Fields |
|---|---|---|
| `web_search` | SearXNG general | `query`, `limit` |
| `news_search` | SearXNG news | `query`, `days`, `limit` |
| `court_search` | CourtListener (federal RECAP) | `party_name`, `court`, `limit` |
| `filing_search` | SEC EDGAR (includes Form D) | `query`, `forms`, `limit` |
| `economic_search` | FRED | `query`, `limit` |
| `entity_background` | news + court + filings in parallel | `name`, `days`, `limit` |

## Response

```json
{
  "task_id": "…",
  "status": "ok | partial | error",
  "results": {
    "news": {"status": "ok", "items": [...], "latency_ms": 412, "cached": false},
    "court": {"status": "timeout", "error": "resource timed out", ...}
  }
}
```

Each step ends as `ok`, `timeout`, `error`, `skipped`, or `circuit_open`. Requests
never hang past their deadline.

## Behavior

- **Deadlines:** each request has a budget. Default 15 s, capped at `MAX_DEADLINE_SECONDS`.
- **Retries:** one retry for network errors and 5xx responses. Rate limits (429) are not retried.
- **Circuit breaker:** a resource that fails `BREAKER_THRESHOLD` times in a row pauses for `BREAKER_COOLDOWN_SECONDS`.
- **Concurrency:** at most `RESOURCE_CONCURRENCY` calls per resource at once.
- **Cache:** results cache for `CACHE_TTL_SECONDS`, in Redis if `REDIS_URL` is set, else in memory.
- **Logging:** logs task type, step, status, and latency. It never logs query text or parameters.
- **Storage:** no Postgres writes. Under the Data Manager rule, Postgres writes go through the Data Manager.

## Configuration

All settings come from environment variables set in Coolify. Nothing is hardcoded in
compose files or code. See `.env.example`.

Tuning defaults if unset: `DEFAULT_DEADLINE_SECONDS=15`, `MAX_DEADLINE_SECONDS=60`,
`RESOURCE_CONCURRENCY=4`, `CACHE_TTL_SECONDS=900`, `BREAKER_THRESHOLD=3`,
`BREAKER_COOLDOWN_SECONDS=60`.

A resource loads only when its setting is present: SearXNG needs `SEARXNG_URL`, EDGAR
needs `SEC_USER_AGENT`, FRED needs `FRED_API_KEY`, and CourtListener needs
`COURTLISTENER_TOKEN`. CourtListener rejects anonymous requests, so `court_search` stays
unavailable until a token is set.

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
SEARCH_MANAGER_API_KEY=dev-key pytest -q
SEARCH_MANAGER_API_KEY=dev-key uvicorn app.main:app --port 8000
```

## Not yet verified

The tests use fake resources. The SearXNG, CourtListener, EDGAR, and FRED adapters
still need a live check against each real API. Check each response shape before
relying on it.

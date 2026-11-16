"""Turns a plain-language request into a structured task.

The n8n workflow "Search Rank" (webhook search-intake) calls the LiteLLM Operations
model with the search-intake instructions from NocoDB. This service never calls
LiteLLM directly. The model only fills task fields. It never picks resources,
timeouts, or retries.
"""

from __future__ import annotations

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.models import Task, task_adapter

RANK_HEADER = "X-Search-Key"


class IntakeError(Exception):
    pass


class NeedsClarification(Exception):
    def __init__(self, question: str):
        super().__init__(question)
        self.question = question


async def plain_language_to_task(
    client: httpx.AsyncClient, settings: Settings, request_text: str
) -> Task:
    if not (settings.n8n_intake_url and settings.api_key):
        raise IntakeError("plain-language requests are not configured on this server")

    body = {
        "request": request_text,
        "schema": task_adapter.json_schema(),
    }
    try:
        resp = await client.post(
            settings.n8n_intake_url,
            json=body,
            headers={RANK_HEADER: settings.api_key},
            timeout=30.0,
        )
    except httpx.TransportError as exc:
        raise IntakeError("intake service unreachable") from exc
    if resp.status_code >= 400:
        raise IntakeError(f"intake service returned {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise IntakeError("intake service returned unreadable output") from exc

    if not isinstance(data, dict):
        raise IntakeError("intake service returned unreadable output")
    if "clarify" in data:
        raise NeedsClarification(str(data["clarify"]))
    if "error" in data:
        raise IntakeError(str(data["error"]))

    try:
        return task_adapter.validate_python(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise IntakeError(f"intake output failed validation: {first['msg']}") from exc

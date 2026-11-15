"""Turns a plain-language request into a structured task using the LiteLLM gateway.

The model only fills task fields. It never picks resources, timeouts, or retries.
"""

from __future__ import annotations

import json

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.models import Task, task_adapter

SYSTEM_PROMPT = """You convert a user's search request into exactly one JSON task.
Return only a JSON object. Use one of these task schemas (JSON Schema):
{schema}

If the request lacks information that a schema requires, return
{{"clarify": "<one short question>"}} instead.
Do not add fields outside the schema. Do not choose sources or settings."""


class IntakeError(Exception):
    pass


class NeedsClarification(Exception):
    def __init__(self, question: str):
        super().__init__(question)
        self.question = question


async def plain_language_to_task(
    client: httpx.AsyncClient, settings: Settings, request_text: str
) -> Task:
    if not (settings.litellm_url and settings.intake_model):
        raise IntakeError("plain-language requests are not configured on this server")

    schema = json.dumps(task_adapter.json_schema())
    body = {
        "model": settings.intake_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.format(schema=schema)},
            {"role": "user", "content": request_text},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {}
    if settings.litellm_api_key:
        headers["Authorization"] = f"Bearer {settings.litellm_api_key}"

    try:
        resp = await client.post(
            f"{settings.litellm_url.rstrip('/')}/v1/chat/completions",
            json=body,
            headers=headers,
            timeout=20.0,
        )
    except httpx.TransportError as exc:
        raise IntakeError("intake model unreachable") from exc
    if resp.status_code >= 400:
        raise IntakeError(f"intake model returned {resp.status_code}")

    try:
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise IntakeError("intake model returned unreadable output") from exc

    if isinstance(data, dict) and "clarify" in data:
        raise NeedsClarification(str(data["clarify"]))

    try:
        return task_adapter.validate_python(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise IntakeError(f"intake output failed validation: {first['msg']}") from exc

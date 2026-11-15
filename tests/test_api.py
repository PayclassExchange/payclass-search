import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import get_settings
from app.executor import CircuitBreaker, Executor
from app.cache import Cache
from app.resources.base import Resource


class EchoResource(Resource):
    name = "searxng_general"
    timeout_s = 1.0

    async def search(self, params):
        return [{"title": f"hit for {params['query']}"}]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SEARCH_MANAGER_API_KEY", "test-key")
    get_settings.cache_clear()
    with TestClient(main.app) as c:
        main.app.state.executor = Executor(
            {"searxng_general": EchoResource(client=None)},
            Cache(None, ttl_s=60),
            CircuitBreaker(threshold=3, cooldown_s=60),
            concurrency=4,
        )
        yield c
    get_settings.cache_clear()


def test_health_needs_no_key(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_rejects_missing_key(client):
    resp = client.post("/v1/tasks", json={"task": {"type": "web_search", "query": "hi there"}})
    assert resp.status_code == 401


def test_rejects_wrong_key(client):
    resp = client.post(
        "/v1/tasks",
        json={"task": {"type": "web_search", "query": "hi there"}},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_structured_task_returns_ok(client):
    resp = client.post(
        "/v1/tasks",
        json={"task": {"type": "web_search", "query": "payclass"}},
        headers={"Authorization": "Bearer test-key"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "ok"
    assert body["results"]["web"]["items"] == [{"title": "hit for payclass"}]


def test_unknown_task_type_returns_422(client):
    resp = client.post(
        "/v1/tasks",
        json={"task": {"type": "people_search", "name": "x"}},
        headers={"Authorization": "Bearer test-key"},
    )
    assert resp.status_code == 422


def test_requires_exactly_one_input(client):
    resp = client.post("/v1/tasks", json={}, headers={"Authorization": "Bearer test-key"})
    assert resp.status_code == 422


def test_plain_language_not_configured_returns_422(client):
    resp = client.post(
        "/v1/tasks",
        json={"request": "news about acme"},
        headers={"Authorization": "Bearer test-key"},
    )
    assert resp.status_code == 422

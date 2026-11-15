import asyncio

from app.cache import Cache
from app.executor import CircuitBreaker, Executor
from app.planner import Step
from app.resources.base import Resource, ResourceError


class OkResource(Resource):
    name = "ok"
    timeout_s = 1.0

    async def search(self, params):
        return [{"title": params["query"]}]


class SlowResource(Resource):
    name = "slow"
    timeout_s = 0.05

    async def search(self, params):
        await asyncio.sleep(1)
        return []


class FlakyResource(Resource):
    name = "flaky"
    timeout_s = 1.0
    calls = 0

    async def search(self, params):
        FlakyResource.calls += 1
        if FlakyResource.calls == 1:
            raise ResourceError("upstream 503", retryable=True)
        return [{"title": "recovered"}]


class BrokenResource(Resource):
    name = "broken"
    timeout_s = 1.0

    async def search(self, params):
        raise ResourceError("upstream 400")


def make_executor(resources, threshold=3):
    return Executor(
        resources=resources,
        cache=Cache(None, ttl_s=60),
        breaker=CircuitBreaker(threshold=threshold, cooldown_s=60),
        concurrency=4,
    )


def run(coro):
    return asyncio.run(coro)


def test_ok_step_returns_items():
    ex = make_executor({"ok": OkResource(client=None)})
    results = run(ex.run([Step("a", "ok", {"query": "hi"})], deadline_s=5))
    assert results["a"].status == "ok"
    assert results["a"].items == [{"title": "hi"}]


def test_slow_resource_times_out_without_hanging():
    ex = make_executor({"slow": SlowResource(client=None)})
    results = run(ex.run([Step("a", "slow", {"query": "hi"})], deadline_s=5))
    assert results["a"].status == "timeout"


def test_retryable_error_is_retried_once():
    FlakyResource.calls = 0
    ex = make_executor({"flaky": FlakyResource(client=None)})
    results = run(ex.run([Step("a", "flaky", {"query": "hi"})], deadline_s=5))
    assert results["a"].status == "ok"
    assert FlakyResource.calls == 2


def test_dependent_step_is_skipped_when_dependency_fails():
    ex = make_executor({"broken": BrokenResource(client=None), "ok": OkResource(client=None)})
    steps = [
        Step("first", "broken", {"query": "hi"}),
        Step("second", "ok", {"query": "hi"}, depends_on=["first"]),
    ]
    results = run(ex.run(steps, deadline_s=5))
    assert results["first"].status == "error"
    assert results["second"].status == "skipped"


def test_circuit_opens_after_repeated_failures():
    ex = make_executor({"broken": BrokenResource(client=None)}, threshold=2)
    steps = [Step(str(i), "broken", {"query": "hi"}) for i in range(3)]
    results = run(ex.run(steps, deadline_s=5))
    statuses = sorted(r.status for r in results.values())
    assert statuses.count("circuit_open") >= 1


def test_unconfigured_resource_returns_error():
    ex = make_executor({})
    results = run(ex.run([Step("a", "missing", {"query": "hi"})], deadline_s=5))
    assert results["a"].status == "error"

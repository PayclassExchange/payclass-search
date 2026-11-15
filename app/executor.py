"""Runs plan steps with deadlines, retries, concurrency limits, and circuit breakers.

Callers never see a hung request: every step ends as ok, timeout, error,
skipped, or circuit_open, and the whole task ends before its deadline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.cache import Cache, make_key
from app.planner import Step
from app.resources.base import Resource, ResourceError

logger = logging.getLogger("search_manager.executor")


@dataclass
class StepResult:
    id: str
    resource: str
    status: str
    items: list[dict] = field(default_factory=list)
    error: str | None = None
    latency_ms: int = 0
    cached: bool = False


class CircuitBreaker:
    """Skips a resource for a cooldown after repeated failures.

    State is per process. Running more than one replica gives each replica its own breaker.
    """

    def __init__(self, threshold: int, cooldown_s: float):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def allow(self, name: str) -> bool:
        return time.monotonic() >= self._open_until.get(name, 0.0)

    def record_success(self, name: str) -> None:
        self._failures[name] = 0

    def record_failure(self, name: str) -> None:
        count = self._failures.get(name, 0) + 1
        self._failures[name] = count
        if count >= self.threshold:
            self._open_until[name] = time.monotonic() + self.cooldown_s
            self._failures[name] = 0
            logger.warning("circuit opened for resource %s", name)


class Executor:
    def __init__(
        self,
        resources: dict[str, Resource],
        cache: Cache,
        breaker: CircuitBreaker,
        concurrency: int,
    ):
        self.resources = resources
        self.cache = cache
        self.breaker = breaker
        self._sems = {name: asyncio.Semaphore(concurrency) for name in resources}

    async def run(self, steps: list[Step], deadline_s: float) -> dict[str, StepResult]:
        t_end = time.monotonic() + deadline_s
        results: dict[str, StepResult] = {}
        pending = {s.id: s for s in steps}

        while pending:
            batch: list[Step] = []
            progressed = False

            for step in list(pending.values()):
                if not all(d in results for d in step.depends_on):
                    continue
                del pending[step.id]
                progressed = True
                if any(results[d].status != "ok" for d in step.depends_on):
                    results[step.id] = StepResult(
                        step.id, step.resource, "skipped", error="dependency did not succeed"
                    )
                else:
                    batch.append(step)

            if batch:
                finished = await asyncio.gather(*(self._run_step(s, t_end) for s in batch))
                for result in finished:
                    results[result.id] = result

            if not progressed:
                for step in pending.values():
                    results[step.id] = StepResult(
                        step.id, step.resource, "error", error="circular dependency"
                    )
                break

        return results

    async def _run_step(self, step: Step, t_end: float) -> StepResult:
        start = time.monotonic()

        if step.resource not in self.resources:
            return StepResult(step.id, step.resource, "error", error="resource not configured")

        if not self.breaker.allow(step.resource):
            return StepResult(step.id, step.resource, "circuit_open", error="resource paused")

        key = make_key(step.resource, step.params)
        cached = await self.cache.get(key)
        if cached is not None:
            return StepResult(step.id, step.resource, "ok", items=cached, cached=True)

        async with self._sems[step.resource]:
            result = await self._call_with_retry(step, t_end)

        result.latency_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "step=%s resource=%s status=%s latency_ms=%d",
            step.id,
            step.resource,
            result.status,
            result.latency_ms,
        )

        if result.status == "ok":
            self.breaker.record_success(step.resource)
            await self.cache.set(key, result.items)
        elif result.status in ("timeout", "error"):
            self.breaker.record_failure(step.resource)
        return result

    async def _call_with_retry(self, step: Step, t_end: float) -> StepResult:
        resource = self.resources[step.resource]
        attempts = 2

        for attempt in range(attempts):
            remaining = t_end - time.monotonic()
            if remaining <= 0:
                return StepResult(step.id, step.resource, "timeout", error="deadline reached")

            try:
                items = await asyncio.wait_for(
                    resource.search(step.params),
                    timeout=min(remaining, resource.timeout_s),
                )
                return StepResult(step.id, step.resource, "ok", items=items)
            except asyncio.TimeoutError:
                return StepResult(step.id, step.resource, "timeout", error="resource timed out")
            except ResourceError as exc:
                time_left = t_end - time.monotonic()
                if exc.retryable and attempt < attempts - 1 and time_left > 1.0:
                    await asyncio.sleep(0.5)
                    continue
                return StepResult(step.id, step.resource, "error", error=str(exc))
            except Exception:
                logger.exception("unexpected failure in resource %s", step.resource)
                return StepResult(step.id, step.resource, "error", error="unexpected failure")

        return StepResult(step.id, step.resource, "error", error="retries exhausted")

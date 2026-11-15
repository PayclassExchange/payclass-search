"""Turns one task into resource steps. Steps with no depends_on run in parallel."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import (
    CourtSearch,
    EconomicSearch,
    EntityBackground,
    FilingSearch,
    NewsSearch,
    Task,
    WebSearch,
)

SEARXNG_GENERAL = "searxng_general"
SEARXNG_NEWS = "searxng_news"
COURTLISTENER = "courtlistener"
SEC_EDGAR = "sec_edgar"
FRED = "fred"


@dataclass
class Step:
    id: str
    resource: str
    params: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


def plan(task: Task) -> list[Step]:
    if isinstance(task, WebSearch):
        return [Step("web", SEARXNG_GENERAL, {"query": task.query, "limit": task.limit})]

    if isinstance(task, NewsSearch):
        return [
            Step(
                "news",
                SEARXNG_NEWS,
                {"query": task.query, "days": task.days, "limit": task.limit},
            )
        ]

    if isinstance(task, CourtSearch):
        return [
            Step(
                "court",
                COURTLISTENER,
                {"party_name": task.party_name, "court": task.court, "limit": task.limit},
            )
        ]

    if isinstance(task, FilingSearch):
        return [
            Step(
                "filings",
                SEC_EDGAR,
                {"query": task.query, "forms": task.forms, "limit": task.limit},
            )
        ]

    if isinstance(task, EconomicSearch):
        return [Step("economic", FRED, {"query": task.query, "limit": task.limit})]

    if isinstance(task, EntityBackground):
        # The three lookups are independent, so they run in parallel.
        # To run one after another, add depends_on to the later step.
        return [
            Step(
                "news",
                SEARXNG_NEWS,
                {"query": task.name, "days": task.days, "limit": task.limit},
            ),
            Step(
                "court",
                COURTLISTENER,
                {"party_name": task.name, "court": None, "limit": task.limit},
            ),
            Step(
                "filings",
                SEC_EDGAR,
                {"query": task.name, "forms": [], "limit": task.limit},
            ),
        ]

    raise ValueError("unknown task type")

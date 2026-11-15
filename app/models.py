"""Task types a caller can send. Each task type is one structured request."""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter


class WebSearch(BaseModel):
    type: Literal["web_search"]
    query: str = Field(min_length=2, max_length=300)
    limit: int = Field(default=10, ge=1, le=50)


class NewsSearch(BaseModel):
    type: Literal["news_search"]
    query: str = Field(min_length=2, max_length=300)
    days: int = Field(default=30, ge=1, le=3650)
    limit: int = Field(default=10, ge=1, le=50)


class CourtSearch(BaseModel):
    type: Literal["court_search"]
    party_name: str = Field(min_length=2, max_length=200)
    court: Optional[str] = Field(default=None, max_length=20)
    limit: int = Field(default=10, ge=1, le=50)


class FilingSearch(BaseModel):
    type: Literal["filing_search"]
    query: str = Field(min_length=2, max_length=200)
    forms: list[str] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=10, ge=1, le=50)


class EconomicSearch(BaseModel):
    type: Literal["economic_search"]
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)


class EntityBackground(BaseModel):
    """Composite task: the planner fans this out to news, court, and filing lookups."""

    type: Literal["entity_background"]
    name: str = Field(min_length=2, max_length=200)
    days: int = Field(default=365, ge=1, le=3650)
    limit: int = Field(default=10, ge=1, le=50)


Task = Annotated[
    Union[
        WebSearch,
        NewsSearch,
        CourtSearch,
        FilingSearch,
        EconomicSearch,
        EntityBackground,
    ],
    Field(discriminator="type"),
]

task_adapter: TypeAdapter = TypeAdapter(Task)

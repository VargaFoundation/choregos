"""La base des DTO et la pagination."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Dto(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class PageMeta(Dto):
    next_cursor: str | None = None
    has_more: bool = False

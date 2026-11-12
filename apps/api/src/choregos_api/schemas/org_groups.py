# SPDX-License-Identifier: Apache-2.0
"""An organisation's groups (ADR 0152): the bodies of `/orgs/{org}/groups…`."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import Dto

#: A group's slug: what a workflow's human actor names (`group: release-captains`).
GROUP_SLUG_PATTERN = r"^[a-z][a-z0-9-]{1,62}$"

OrgGroupSource = Literal["internal", "scim"]


class OrgGroupCreate(Dto):
    slug: Annotated[str, Field(pattern=GROUP_SLUG_PATTERN)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=2000)] | None = None


class OrgGroupUpdate(Dto):
    """Only the fields sent change; `description: null` clears it."""

    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None


class OrgGroupDto(Dto):
    slug: str
    name: str
    description: str | None = None
    source: OrgGroupSource
    member_count: int = 0
    created_by: str | None = None
    created_at: datetime


class OrgGroupList(Dto):
    items: list[OrgGroupDto]


class OrgGroupMemberAdd(Dto):
    """One of the two: the person's e-mail or their user id. Either way, a member of the organisation."""

    email: Annotated[str, Field(min_length=3, max_length=320)] | None = None
    user_id: Annotated[str, Field(min_length=1, max_length=36)] | None = None

    @model_validator(mode="after")
    def _one_of_the_two(self) -> OrgGroupMemberAdd:
        if (self.email is None) == (self.user_id is None):
            raise ValueError("name the person by `email` or by `user_id`, not both and not neither")
        return self


class OrgGroupMemberDto(Dto):
    user_id: str
    email: str
    display_name: str = ""
    added_by: str | None = None
    added_at: datetime


class OrgGroupMemberList(Dto):
    items: list[OrgGroupMemberDto]

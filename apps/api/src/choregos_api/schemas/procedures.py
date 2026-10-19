# SPDX-License-Identifier: Apache-2.0
"""Setup procedures (ADR 0067): what to do at the vendor, then in Choregos, to add one thing."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from .base import Dto

Audience = Literal["org_admin", "project_owner", "operator", "everyone"]


class ProcedureSummary(Dto):
    id: str
    title: str
    version: int
    audience: Audience
    #: `connector`, `source`, `agent` or `door_client` -> what it applies to; plus `card_host` or
    #: `preset` when it is a preset.
    applies_to: dict[str, str]
    #: An in-app route that IS the procedure (the MCP-door clients' page), else null.
    link: str | None = None


class ProcedureSection(Dto):
    #: `before`, `at`, `permissions`, `in_choregos`, `test`, `fails` or `site`.
    key: str
    title: str
    markdown: str


class VendorDocDto(Dto):
    title: str
    url: str


class ProcedureDto(ProcedureSummary):
    reviewed: date
    sections: list[ProcedureSection]
    #: The copy boxes, with this deployment's URLs: `{"webhook_url:jira": "https://…"}`.
    values: dict[str, str] = Field(default_factory=dict)
    #: Hosts the setup needs to reach (the egress allowlist).
    egress: list[str] = Field(default_factory=list)
    #: The form fields the procedure talks about, with what to paste in each.
    fields: dict[str, str] = Field(default_factory=dict)
    #: The only links the sections may carry; the console renders any other as text.
    vendor_docs: list[VendorDocDto] = Field(default_factory=list)
    #: `shipped`, `extra` (this deployment's directory) or `plugin:<name>`.
    origin: str = "shipped"

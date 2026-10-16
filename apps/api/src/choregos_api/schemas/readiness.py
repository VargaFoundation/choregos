# SPDX-License-Identifier: Apache-2.0
"""A project's readiness: the DTOs of `openapi.yaml` (S40-06)."""

from __future__ import annotations

from typing import Literal

from .base import Dto

Section = Literal["general", "connections", "agents", "guardrails", "people", "notifications", "tools"]
State = Literal["done", "check", "todo", "not_needed"]
Fixer = Literal["project_owner", "org_admin", "platform_operator", "external"]


class ReadinessFix(Dto):
    href: str | None = None
    by: Fixer


class ReadinessItem(Dto):
    id: str
    section: Section
    title: str
    state: State
    detail: str
    fix: ReadinessFix


class ReadinessSummary(Dto):
    done: int = 0
    check: int = 0
    todo: int = 0


class ProjectReadiness(Dto):
    ready: bool
    summary: ReadinessSummary
    items: list[ReadinessItem]

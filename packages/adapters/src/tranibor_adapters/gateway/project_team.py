# SPDX-License-Identifier: Apache-2.0
"""A project's gateway team, when the gateway can hold one (ADR 0047, ADR 0205)."""

from __future__ import annotations

from typing import Any


async def ensure_project_team(gateway: Any, team_id: str, daily_usd: float) -> str | None:
    """The team that carries the project's day budget, or None when the gateway has no `teams`.

    A gateway reached with a delegated key (ADR 0205) cannot create a team and its keys cannot join
    one. The day is then held by the platform alone: `prepare_stage` subtracts the project's measured
    spend and its open reservations from every key it mints, so a project over its day is refused
    before a key exists. Only the second line of defence, the gateway cutting the day mid-run, is lost.
    """
    if "teams" not in getattr(gateway, "capabilities", frozenset()):
        return None
    return str(await gateway.ensure_team(team_id, daily_usd))

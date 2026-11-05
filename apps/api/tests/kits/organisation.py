"""Organisation helpers shared by the rename tests."""

from __future__ import annotations


async def declare_administrator(emails: set[str]) -> None:
    """Make an installed seam name `emails` as platform administrators (replacing any other)."""
    from choregos_api.edition import declare_platform_administrator, reset

    reset()
    declare_platform_administrator(lambda session, principal: principal.email in emails)

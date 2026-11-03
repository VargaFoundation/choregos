"""Organisation helpers shared by the rename tests."""

from __future__ import annotations


async def declare_administrator(emails: set[str]) -> None:
    """Make an installed seam name `emails` as platform administrators (replacing any other)."""
    from choregos_api.edition import declarer_un_administrateur_de_plateforme, reinitialiser

    reinitialiser()
    declarer_un_administrateur_de_plateforme(lambda session, principal: principal.email in emails)

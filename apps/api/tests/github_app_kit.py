"""Shared decor of the GitHub App tests (ADR 0073)."""

from __future__ import annotations


async def add_organisation(slug: str) -> str:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        org = Organization(slug=slug, name=slug.title())
        session.add(org)
        await session.flush()
        return str(org.id)


async def claim_for(installation_id: int, login: str, org_id: str | None) -> None:
    """Write an installation row as the platform would have (claimed by `org_id`, or unclaimed)."""
    from choregos_api.db.models import GitHubInstallation
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        session.add(
            GitHubInstallation(
                id=installation_id,
                account_login=login,
                account_key=login.lower(),
                account_id=installation_id * 10,
                account_type="Organization",
                app_id="1",
                repository_selection="selected",
                org_id=org_id,
            )
        )

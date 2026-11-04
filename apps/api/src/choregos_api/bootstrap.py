# SPDX-License-Identifier: Apache-2.0
"""Bootstrap of a new installation: the first organisation and its administrators.

An empty database has neither organisation nor member. `POST /orgs/{org}/projects` answers 404,
`POST /orgs` demands an administrator who does not exist yet, and the OIDC group mapping has
nothing to attach a role to. Before 2026-09-24 only `dev/scripts/seed.py` and `demo/seed.py` built
an organisation: an installation through the chart was unusable without touching the database.

This module reads `CHOREGOS_BOOTSTRAP_ORG` and `CHOREGOS_BOOTSTRAP_ADMINS` when the API starts. It
creates what is missing and does not modify what exists: an administrator removed by hand does not
come back at the next restart.
"""

from __future__ import annotations

from choregos_contracts import Role
from sqlalchemy import select

from .config import Settings
from .db.models import Membership, Organization, User
from .db.org_aliases import organisation_by_slug_or_alias
from .db.session import session_scope
from .logging import get_logger

logger = get_logger("choregos.bootstrap")

#: The keys of what `bootstrap_installation` returns. `admin_cli.py` and the installation tests read
#: the second one under its former name, so it stays until their own stories switch it.
ORGANISATIONS = "organisations"
ADMINISTRATORS = "administrateurs"  # lang: fr-ok returned key that admin_cli.py and the tests read


async def bootstrap_installation(settings: Settings) -> dict[str, list[str]]:
    """Creates the bootstrap organisation and its administrators if missing. Returns what was created."""
    if not settings.bootstrap_org:
        return {ORGANISATIONS: [], ADMINISTRATORS: []}
    created: dict[str, list[str]] = {ORGANISATIONS: [], ADMINISTRATORS: []}
    async with session_scope(orgs="*") as session:
        # By slug OR by former slug: once the organisation has been renamed, the deployment's value
        # still names the old one, and a restart must not create a second organisation (ADR 0090).
        org = await organisation_by_slug_or_alias(session, settings.bootstrap_org)
        if org is None:
            org = Organization(
                slug=settings.bootstrap_org, name=settings.bootstrap_org_name or settings.bootstrap_org
            )
            session.add(org)
            await session.flush()
            created[ORGANISATIONS].append(org.slug)
            for email in settings.bootstrap_admin_emails:
                user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
                if user is None:
                    user = User(email=email, display_name=email.split("@", 1)[0])
                    session.add(user)
                    await session.flush()
                session.add(Membership(user_id=user.id, org_id=org.id, role=str(Role.ORG_ADMIN)))
                created[ADMINISTRATORS].append(email)
    if created[ORGANISATIONS]:
        logger.info(
            "installation bootstrapped", organisation=settings.bootstrap_org, admins=created[ADMINISTRATORS]
        )
    return created

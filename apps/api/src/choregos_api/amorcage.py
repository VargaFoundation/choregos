"""Amorçage d'une installation neuve : la première organisation et ses administrateurs.

Une base vide n'a ni organisation ni membre. `POST /orgs/{org}/projects` répond 404,
`POST /orgs` exige un administrateur qui n'existe pas encore, et le mapping des groupes
OIDC n'a rien où rattacher un rôle. Avant le 2026-09-24, seuls `dev/scripts/seed.py` et
`demo/seed.py` construisaient une organisation : une installation par le chart était
inutilisable sans toucher à la base.

Ce module lit `CHOREGOS_BOOTSTRAP_ORG` et `CHOREGOS_BOOTSTRAP_ADMINS` au démarrage de
l'API. Il crée ce qui manque et ne modifie pas ce qui existe : un administrateur retiré
à la main ne revient pas au prochain redémarrage.
"""

from __future__ import annotations

from choregos_contracts import Role
from sqlalchemy import select

from .config import Settings
from .db.models import Membership, Organization, User
from .db.session import session_scope
from .logging import get_logger

logger = get_logger("choregos.amorcage")


async def amorcer(settings: Settings) -> dict[str, list[str]]:
    """Crée l'organisation d'amorçage et ses administrateurs s'ils manquent. Rend ce qui a été créé."""
    if not settings.bootstrap_org:
        return {"organisations": [], "administrateurs": []}
    crees: dict[str, list[str]] = {"organisations": [], "administrateurs": []}
    async with session_scope(orgs="*") as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == settings.bootstrap_org))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(
                slug=settings.bootstrap_org, name=settings.bootstrap_org_name or settings.bootstrap_org
            )
            session.add(org)
            await session.flush()
            crees["organisations"].append(org.slug)
            for email in settings.bootstrap_admin_emails:
                user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
                if user is None:
                    user = User(email=email, display_name=email.split("@", 1)[0])
                    session.add(user)
                    await session.flush()
                session.add(Membership(user_id=user.id, org_id=org.id, role=str(Role.ORG_ADMIN)))
                crees["administrateurs"].append(email)
    if crees["organisations"]:
        logger.info(
            "installation amorcée", organisation=settings.bootstrap_org, admins=crees["administrateurs"]
        )
    return crees

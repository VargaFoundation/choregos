# SPDX-License-Identifier: Apache-2.0
"""Qui frappe à la porte : un jeton à portée MCP, l'humain qui l'a frappé, et son projet éventuel."""

from __future__ import annotations

from dataclasses import dataclass

from choregos_core import utcnow
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiToken, Organization, Project, User
from ..db.session import TOUT, limiter_aux_organisations
from ..deps import _aware, _principal_from_user, noter_l_usage
from ..rbac import Principal
from ..security import hash_api_token

PORTEES_MCP = frozenset({"mcp:read", "mcp:write"})


class Refus(Exception):  # noqa: N818 - une décision de la porte, pas une panne
    """La porte refuse avant tout échange MCP : rendue en HTTP, avec `WWW-Authenticate`."""

    def __init__(self, statut: int, message: str, *, erreur: str | None = None) -> None:
        super().__init__(message)
        self.statut = statut
        self.erreur = erreur


@dataclass(slots=True)
class Appelant:
    principal: Principal
    jeton: ApiToken
    portees: frozenset[str]
    projet_lie: Project | None = None
    org_du_projet_lie: str | None = None

    @property
    def ecrit(self) -> bool:
        return "mcp:write" in self.portees


async def identifier(session: AsyncSession, request: Request) -> Appelant:
    """Le jeton porteur, lu en portée de plateforme ; le principal borne ensuite la RLS."""
    entete = request.headers.get("authorization", "")
    if not entete.lower().startswith("bearer "):
        raise Refus(401, "jeton manquant : Authorization: Bearer chg_… (portée mcp:read ou mcp:write)")
    brut = entete.split(" ", 1)[1].strip()
    await limiter_aux_organisations(session, TOUT)
    jeton = (
        await session.execute(select(ApiToken).where(ApiToken.hash == hash_api_token(brut)))
    ).scalar_one_or_none()
    if jeton is None:
        raise Refus(401, "jeton inconnu ou révoqué", erreur="invalid_token")
    if jeton.expires_at is not None and _aware(jeton.expires_at) <= utcnow():
        raise Refus(401, "jeton expiré", erreur="invalid_token")
    portees = frozenset(jeton.scopes or ["*"])
    if not portees & PORTEES_MCP:
        # Un jeton `*` ouvre l'API REST : on n'en veut pas dans la configuration d'un client.
        raise Refus(
            403,
            "la porte MCP refuse un jeton de portée `*` : "
            "créez un jeton mcp:read ou mcp:write pour ce client",
            erreur="insufficient_scope",
        )
    user = await session.get(User, jeton.user_id)
    if user is None:
        raise Refus(401, "jeton orphelin", erreur="invalid_token")
    noter_l_usage(jeton, request)
    principal = await _principal_from_user(session, user)
    appelant = Appelant(principal=principal, jeton=jeton, portees=portees)
    if jeton.project_id:
        projet = await session.get(Project, jeton.project_id)
        if projet is None:
            raise Refus(401, "le projet de ce jeton n'existe plus", erreur="invalid_token")
        org = await session.get(Organization, projet.org_id)
        appelant.projet_lie, appelant.org_du_projet_lie = projet, org.slug if org else ""
    return appelant

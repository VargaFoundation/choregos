# SPDX-License-Identifier: Apache-2.0
"""Qui frappe à la porte : un jeton à portée MCP, l'humain qui l'a frappé, et son projet éventuel."""

from __future__ import annotations

from dataclasses import dataclass

from choregos_core import utcnow
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import ApiToken, Organization, Project, User
from ..db.session import TOUT, limiter_aux_organisations
from ..deps import _aware, _principal_from_user, noter_l_usage
from ..rbac import Principal
from ..security import hash_api_token
from .oauth import JetonRefuse, emetteur, valider

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
    #: Le jeton `chg_`, ou `None` pour un jeton OAuth de l'IdP.
    jeton: ApiToken | None
    portees: frozenset[str]
    #: Ce qui identifie le client pour le débit et l'audit : `jeton:<id>` ou `oauth:<azp>:<sub>`.
    cle: str = ""
    projet_lie: Project | None = None
    org_du_projet_lie: str | None = None

    @property
    def ecrit(self) -> bool:
        return "mcp:write" in self.portees


async def identifier(session: AsyncSession, request: Request, settings: Settings) -> Appelant:
    """Le jeton porteur, lu en portée de plateforme ; le principal borne ensuite la RLS."""
    entete = request.headers.get("authorization", "")
    if not entete.lower().startswith("bearer "):
        raise Refus(401, "jeton manquant : Authorization: Bearer chg_… (portée mcp:read ou mcp:write)")
    brut = entete.split(" ", 1)[1].strip()
    await limiter_aux_organisations(session, TOUT)
    if not brut.startswith("chg_") and settings.mcp_oauth_enabled:
        return await _par_oauth(session, brut, settings)
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
    appelant = Appelant(principal=principal, jeton=jeton, portees=portees, cle=f"jeton:{jeton.id}")
    if jeton.project_id:
        projet = await session.get(Project, jeton.project_id)
        if projet is None:
            raise Refus(401, "le projet de ce jeton n'existe plus", erreur="invalid_token")
        org = await session.get(Organization, projet.org_id)
        appelant.projet_lie, appelant.org_du_projet_lie = projet, org.slug if org else ""
    return appelant


async def _par_oauth(session: AsyncSession, brut: str, settings: Settings) -> Appelant:
    """Un jeton émis par l'IdP pour la porte (RFC 9728) : l'humain se retrouve par son `sub`, puis
    par son e-mail vérifié. Il doit s'être connecté une fois à la console : la porte ne crée personne.

    Un `sub` n'a de sens que chez son émetteur : il ne désigne un utilisateur de la console que si la
    porte et la console partagent le même IdP. Sinon, seul l'e-mail vérifié relie les deux."""
    try:
        revendications = await valider(brut, settings)
    except JetonRefuse as refus:
        raise Refus(401, str(refus), erreur="invalid_token") from refus
    sub = str(revendications["sub"])
    user = None
    if emetteur(settings) == settings.oidc_issuer.rstrip("/"):
        user = (await session.execute(select(User).where(User.oidc_sub == sub))).scalar_one_or_none()
    if user is None and revendications.get("email") and revendications.get("email_verified"):
        user = (
            await session.execute(select(User).where(User.email == str(revendications["email"])))
        ).scalar_one_or_none()
    if user is None:
        raise Refus(
            403,
            "utilisateur inconnu de Choregos : connectez-vous une fois à la console",
            erreur="invalid_token",
        )
    demandees = set(str(revendications.get("scope") or "").split()) & PORTEES_MCP
    principal = await _principal_from_user(session, user)
    client = str(revendications.get("azp") or revendications.get("client_id") or "?")
    return Appelant(
        principal=principal,
        jeton=None,
        # Sans portée MCP demandée, la lecture : écrire se demande explicitement.
        portees=frozenset(demandees or {"mcp:read"}),
        cle=f"oauth:{client}:{sub}",
    )

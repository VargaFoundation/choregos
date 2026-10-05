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
    #: L'agent externe que ce client incarne (ADR 0033), s'il est rattaché à l'un.
    agent: str | None = None
    #: Les outils de la porte que la version de l'agent permet (motifs) ; `None` : ceux de l'humain.
    motifs: tuple[str, ...] | None = None

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
    await incarner_l_agent(session, appelant, jeton_id=jeton.id)
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
    appelant = Appelant(
        principal=principal,
        jeton=None,
        # Sans portée MCP demandée, la lecture : écrire se demande explicitement.
        portees=frozenset(demandees or {"mcp:read"}),
        cle=f"oauth:{client}:{sub}",
    )
    await incarner_l_agent(session, appelant, client_id=client)
    return appelant


async def incarner_l_agent(
    session: AsyncSession, appelant: Appelant, *, jeton_id: str | None = None, client_id: str | None = None
) -> None:
    """Un client rattaché à un agent externe (ADR 0033) en porte l'identité et les limites.

    La révocation se relit ICI, à chaque appel : un agent révoqué, suspendu ou expiré reçoit 401 au
    suivant. Ses droits sont ceux de l'humain — le principal ne change pas — intersectés avec les
    outils de la porte que sa version nomme (`mcp_servers` d'un connecteur `choregos`).
    """
    from ..db.models import Agent, AgentCredential, AgentVersion

    condition = (
        AgentCredential.api_token_id == jeton_id
        if jeton_id is not None
        else (AgentCredential.kind == "oauth_client") & (AgentCredential.client_id == client_id)
    )
    rattachement = (
        await session.execute(select(AgentCredential).where(condition, AgentCredential.revoked_at.is_(None)))
    ).scalar_one_or_none()
    if rattachement is None:
        return
    agent = await session.get(Agent, rattachement.agent_id)
    if agent is None:
        return
    if agent.status != "active" or (agent.expires_at is not None and _aware(agent.expires_at) <= utcnow()):
        from ..services.agents import ETATS

        etat = ETATS.get(agent.status, agent.status) if agent.status != "active" else "expiré"
        raise Refus(
            401, f"l'agent `{agent.slug}` est {etat} : ce client ne passe plus", erreur="invalid_token"
        )
    spec = (
        await session.execute(
            select(AgentVersion.spec)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none() or {}
    motifs = [
        outil
        for serveur in spec.get("mcp_servers", [])
        if serveur.get("connector") == "choregos"
        for outil in serveur.get("tools", [])
    ]
    appelant.agent = agent.slug
    appelant.motifs = (
        tuple(motifs) if any(s.get("connector") == "choregos" for s in spec.get("mcp_servers", [])) else None
    )

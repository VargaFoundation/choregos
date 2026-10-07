# SPDX-License-Identifier: Apache-2.0
"""Le transport de la porte : Streamable HTTP, sans état, en réponses JSON seulement (ADR 0030).

`POST /mcp` et `POST /mcp/projects/{org}:{slug}`. Un `GET` (le flux SSE du serveur) et un `DELETE`
(la fin d'une session) répondent 405 : la porte n'a ni flux ni session, ce que le protocole permet.
Avant tout échange MCP, la porte refuse en HTTP — taille, origine, version, jeton, débit — pour qu'un
client mal configuré lise une raison, et non un silence.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from choregos_core.mcp import ERREUR_DE_SYNTAXE as SYNTAXE
from choregos_core.mcp import (
    METHODE_INCONNUE,
    PARAMETRES_INVALIDES,
    REQUETE_INVALIDE,
    defaut_du_message,
    erreur,
    est_notification,
    negocier,
    ok,
    resultat_texte,
    version_acceptable,
)
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .. import __version__
from ..audit import record
from ..config import Settings
from ..deps import Config, Db, resolve_project
from ..errors import ApiError
from ..logging import get_logger
from ..rbac import Permission
from .appelant import Appelant, Refus, identifier
from .garde_fous import ECRITURES_PAR_JOUR, LIMITEUR, ecritures_du_jour, tronquer
from .oauth import metadonnees
from .outils import INSTRUCTIONS, Contexte, OutilRefuse, annonces

router = APIRouter(include_in_schema=False)
log = get_logger(__name__)

TAILLE_MAX_DU_CORPS = 1024 * 1024


def _origines_admises(settings: Settings) -> set[str]:
    def origine(url: str) -> str:
        morceaux = urlsplit(url)
        return f"{morceaux.scheme}://{morceaux.netloc}"

    return {origine(settings.public_url), origine(settings.api_url)}


def _refus_http(
    statut: int,
    message: str,
    *,
    erreur_oauth: str | None = None,
    settings: Settings | None = None,
    chemin: str = "/mcp",
) -> JSONResponse:
    entetes = {}
    if statut in {401, 403}:
        valeur = 'Bearer realm="choregos"'
        if settings is not None and settings.mcp_oauth_enabled:
            # Claude lit ce pointeur sur un 401 pour trouver l'IdP (RFC 9728). Le document est celui
            # de la porte appelée : sa `resource` doit être l'URL que le client a saisie.
            document = f"{settings.public_url.rstrip('/')}/.well-known/oauth-protected-resource{chemin}"
            valeur += f', resource_metadata="{document}"'
        if erreur_oauth:
            valeur += f', error="{erreur_oauth}"'
        entetes["WWW-Authenticate"] = valeur
    return JSONResponse({"error": message}, status_code=statut, headers=entetes)


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/{chemin:path}")
async def metadonnees_de_la_ressource(settings: Config, chemin: str = "mcp") -> Response:
    """Les métadonnées RFC 9728 de la porte ; 404 tant qu'OAuth n'est pas allumé."""
    if not settings.mcp_oauth_enabled:
        return Response(status_code=404)
    return JSONResponse(metadonnees(settings, "/" + chemin.strip("/")))


def _faute(identifiant: Any, code: int, message: str, statut: int = 200) -> JSONResponse:
    return JSONResponse(erreur(identifiant, code, message), status_code=statut)


@router.api_route("/mcp", methods=["GET", "DELETE"])
@router.api_route("/mcp/projects/{projet}", methods=["GET", "DELETE"])
async def sans_flux_ni_session() -> Response:
    """La porte est sans état : ni flux SSE à ouvrir, ni session à fermer."""
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp")
async def porte(request: Request, session: Db, settings: Config) -> Response:
    return await _servir(request, session, settings, None)


@router.post("/mcp/projects/{projet}")
async def porte_du_projet(projet: str, request: Request, session: Db, settings: Config) -> Response:
    return await _servir(request, session, settings, projet)


async def _servir(request: Request, session: Any, settings: Settings, projet_demande: str | None) -> Response:
    origine = request.headers.get("origin")
    if origine and origine not in _origines_admises(settings):
        # Une page web ouverte dans le navigateur de l'humain ne parle pas à sa place (DNS rebinding).
        return _refus_http(403, f"origin refused: {origine}")
    longueur = request.headers.get("content-length")
    if longueur and longueur.isdigit() and int(longueur) > TAILLE_MAX_DU_CORPS:
        return _refus_http(413, "message too large (1 MiB at most)")
    if not version_acceptable(request.headers.get("mcp-protocol-version")):
        return _refus_http(400, f"unsupported protocol version: {request.headers['mcp-protocol-version']}")
    try:
        appelant = await identifier(session, request, settings)
    except Refus as refus:
        return _refus_http(
            refus.statut, str(refus), erreur_oauth=refus.erreur, settings=settings, chemin=request.url.path
        )
    admis, attente = LIMITEUR.admet(appelant.cle)
    if not admis:
        return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": str(attente)})

    projet, org = appelant.projet_lie, appelant.org_du_projet_lie
    if projet_demande is not None:
        try:
            cible, org_cible = await resolve_project(session, projet_demande)
        except ApiError:
            return _refus_http(404, f"project `{projet_demande}` not found")
        if not appelant.principal.can(Permission.PROJECT_READ, org_cible, cible.slug):
            return _refus_http(404, f"project `{projet_demande}` not found")
        if projet is not None and projet.id != cible.id:
            return _refus_http(403, f"this token is bound to project {org}:{projet.slug}")
        projet, org = cible, org_cible

    corps = await request.body()
    if len(corps) > TAILLE_MAX_DU_CORPS:
        return _refus_http(413, "message too large (1 MiB at most)")
    try:
        message = json.loads(corps)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _faute(None, SYNTAXE, "unreadable body: JSON is expected", statut=400)
    defaut = defaut_du_message(message)
    if defaut is not None:
        identifiant = message.get("id") if isinstance(message, dict) else None
        return _faute(identifiant, REQUETE_INVALIDE, defaut, statut=400)
    if est_notification(message):
        return Response(status_code=202)

    ctx = Contexte(
        session=session,
        appelant=appelant,
        projet=projet,
        org=org,
        console=settings.public_url.rstrip("/"),
    )
    reponse = await _repondre(ctx, message, request)
    return JSONResponse(reponse)


async def _repondre(ctx: Contexte, message: dict[str, Any], request: Request) -> dict[str, Any]:
    identifiant = message.get("id")
    methode = message["method"]
    params = message.get("params") or {}
    if methode == "initialize":
        return ok(
            identifiant,
            {
                "protocolVersion": negocier(params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "choregos", "title": "Choregos", "version": __version__},
                "instructions": INSTRUCTIONS,
            },
        )
    if methode == "ping":
        return ok(identifiant, {})
    if methode == "tools/list":
        return ok(identifiant, {"tools": [outil.annonce() for outil in await annonces(ctx)]})
    if methode == "tools/call":
        return await _appeler(ctx, identifiant, params, request)
    return erreur(identifiant, METHODE_INCONNUE, f"unknown method: {methode}")


async def _appeler(
    ctx: Contexte, identifiant: Any, params: dict[str, Any], request: Request
) -> dict[str, Any]:
    nom = params.get("name")
    arguments = params.get("arguments") or {}
    outil = next((o for o in await annonces(ctx) if o.nom == nom), None)
    if outil is None or not isinstance(arguments, dict):
        # Un outil hors des droits n'existe pas pour cet humain : même réponse qu'un outil inconnu.
        return erreur(identifiant, PARAMETRES_INVALIDES, f"Unknown tool: {nom}")
    issue = "ok"
    try:
        if (
            outil.ecriture
            and await ecritures_du_jour(ctx.session, ctx.appelant.principal.user_id) >= ECRITURES_PAR_JOUR
        ):
            # Toute écriture compte : un ticket ouvert, une action proposée (ADR 0030).
            raise OutilRefuse(f"budget_exhausted: {ECRITURES_PAR_JOUR} writes a day through MCP")
        texte, structure = await outil.executer(ctx, arguments)
        resultat = resultat_texte(tronquer(texte), structure=structure)
    except OutilRefuse as refus:
        issue = "refus"
        resultat = resultat_texte(str(refus), en_erreur=True)
    await _auditer(ctx, outil.nom, outil.ecriture and issue == "ok", issue, request)
    return ok(identifiant, resultat)


async def _auditer(ctx: Contexte, outil: str, ecriture: bool, issue: str, request: Request) -> None:
    """Chaque appel laisse une ligne (R-SOC-MCP-06) — et les écritures du jour s'y comptent."""
    await record(
        ctx.session,
        ctx.appelant.principal,
        "mcp.call",
        # Un appel qui ne vise ni organisation ni projet (`list_projects`) s'inscrit dans celle de
        # l'agent qu'incarne le client : sans organisation, sous RLS, la ligne n'était lisible par
        # personne — l'audit d'un agent externe disparaissait sur PostgreSQL.
        org_id=ctx.org_id_vise
        or (ctx.projet.org_id if ctx.projet is not None else None)
        or ctx.appelant.agent_org_id,
        target_type="mcp_tool",
        target_id=outil,
        ecriture=ecriture,
        issue=issue,
        jeton=ctx.appelant.cle,
        agent=ctx.appelant.agent,
        client=(request.headers.get("user-agent") or "")[:100] or None,
    )
    log.info("mcp.call", outil=outil, issue=issue, appelant=ctx.appelant.cle)


__all__ = ["Appelant", "router"]

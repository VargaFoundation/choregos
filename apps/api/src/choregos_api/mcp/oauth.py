# SPDX-License-Identifier: Apache-2.0
"""La porte MCP, serveur de ressources OAuth (RFC 9728) : l'IdP émet les jetons (ADR 0030).

claude.ai, Claude Desktop et les autres clients hébergés se connectent par OAuth : ils lisent les
métadonnées de la ressource, trouvent l'IdP, y obtiennent un jeton pour l'utilisateur, et le
présentent. La plateforme n'émet aucun secret pour eux (R-SOC-MCP-02) ; elle VÉRIFIE :

- la signature, par le JWKS que publie l'émetteur (mis en cache ; un `kid` inconnu le recharge une
  fois, pour suivre une rotation de clés) ;
- l'émetteur, l'expiration, et l'**audience** de la porte — obligatoire : sur un realm partagé, un
  jeton obtenu par une autre application ne doit pas l'ouvrir ;
- les seuls algorithmes asymétriques : `alg: none` et HS256 sont refusés.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt

from ..config import Settings

ALGORITHMES = ("RS256", "RS384", "RS512", "ES256", "ES384", "PS256")
DUREE_DU_CACHE = 3600.0


class JetonRefuse(Exception):  # noqa: N818 - un refus motivé, rendu en 401
    pass


def emetteur(settings: Settings) -> str:
    return (settings.mcp_oauth_issuer or settings.oidc_issuer).rstrip("/")


def metadonnees(settings: Settings, chemin: str = "/mcp") -> dict[str, Any]:
    """Le document RFC 9728. `resource` est EXACTEMENT l'URL que l'utilisateur saisit."""
    return {
        "resource": f"{settings.public_url.rstrip('/')}{chemin}",
        "authorization_servers": [emetteur(settings)],
        "scopes_supported": ["mcp:read", "mcp:write"],
        "bearer_methods_supported": ["header"],
        "resource_name": "Choregos",
    }


class _Cles:
    """Les clés publiques de l'émetteur, mises en cache."""

    def __init__(self) -> None:
        self._par_emetteur: dict[str, tuple[float, dict[str, jwt.PyJWK]]] = {}

    async def _charger(self, issuer: str) -> dict[str, jwt.PyJWK]:
        async with httpx.AsyncClient(timeout=10) as http:
            reponse = await http.get(f"{issuer}/.well-known/openid-configuration")
            decouverte = reponse.raise_for_status().json()
            jeu = (await http.get(decouverte["jwks_uri"])).raise_for_status().json()
        cles = {}
        for brute in jeu.get("keys", []):
            if brute.get("use", "sig") == "sig" and brute.get("kid"):
                try:
                    cles[str(brute["kid"])] = jwt.PyJWK(brute)
                except jwt.PyJWKError:
                    continue
        self._par_emetteur[issuer] = (time.monotonic(), cles)
        return cles

    async def cle(self, issuer: str, kid: str) -> jwt.PyJWK:
        horodatage, cles = self._par_emetteur.get(issuer, (0.0, {}))
        if kid not in cles or time.monotonic() - horodatage > DUREE_DU_CACHE:
            cles = await self._charger(issuer)
        if kid not in cles:
            raise JetonRefuse(f"key `{kid}` unknown to the issuer")
        return cles[kid]

    def vider(self) -> None:
        self._par_emetteur.clear()


CLES = _Cles()


async def valider(brut: str, settings: Settings) -> dict[str, Any]:
    """Les revendications d'un jeton valide, ou `JetonRefuse`."""
    try:
        entete = jwt.get_unverified_header(brut)
    except jwt.PyJWTError as erreur:
        raise JetonRefuse("unreadable token") from erreur
    if entete.get("alg") not in ALGORITHMES:
        raise JetonRefuse(f"algorithm refused: {entete.get('alg')}")
    issuer = emetteur(settings)
    try:
        cle = await CLES.cle(issuer, str(entete.get("kid") or ""))
        revendications: dict[str, Any] = jwt.decode(
            brut,
            key=cle.key,
            algorithms=[str(entete["alg"])],
            audience=settings.mcp_oauth_audience,
            issuer=issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except (jwt.PyJWTError, httpx.HTTPError) as erreur:
        raise JetonRefuse(f"token refused: {erreur}") from erreur
    return revendications

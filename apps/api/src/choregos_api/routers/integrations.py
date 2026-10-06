# SPDX-License-Identifier: Apache-2.0
"""Où connecter un client MCP à cette plateforme (ADR 0030) : ce que la page Integrations affiche."""

from __future__ import annotations

from choregos_core.mcp import VERSIONS
from fastapi import APIRouter

from .. import __version__
from ..deps import Config, Me
from ..mcp.oauth import emetteur
from ..schemas import IntegrationsDto, OAuthClientDto, OAuthDto

router = APIRouter(tags=["session"])


@router.get("/integrations", response_model=IntegrationsDto, operation_id="getIntegrations")
async def integrations(settings: Config, principal: Me) -> IntegrationsDto:
    """L'URL de la porte se déduit de l'URL publique : la console et `/mcp` partagent l'hôte.

    Quand la porte accepte OAuth, la page dit où se connecter et avec quel client : avant, `oauth`
    valait `enabled: false` quoi que dise le déploiement, et la page ne proposait que des jetons."""
    oauth = OAuthDto(enabled=False)
    if settings.mcp_oauth_enabled:
        oauth = OAuthDto(
            enabled=True,
            authorization_server=emetteur(settings),
            clients={
                nom: OAuthClientDto(client_id=client.client_id, callback_port=client.callback_port)
                for nom, client in sorted(settings.mcp_oauth_clients.items())
            },
        )
    return IntegrationsDto(
        mcp_url=f"{settings.public_url.rstrip('/')}/mcp",
        protocol_versions=list(VERSIONS),
        oauth=oauth,
        version=__version__,
    )

# SPDX-License-Identifier: Apache-2.0
"""Où connecter un client MCP à cette plateforme (ADR 0030) : ce que la page Integrations affiche."""

from __future__ import annotations

from choregos_core.mcp import VERSIONS
from fastapi import APIRouter

from .. import __version__
from ..deps import Config, Me
from ..schemas import IntegrationsDto, OAuthDto

router = APIRouter(tags=["session"])


@router.get("/integrations", response_model=IntegrationsDto, operation_id="getIntegrations")
async def integrations(settings: Config, principal: Me) -> IntegrationsDto:
    """L'URL de la porte se déduit de l'URL publique : la console et `/mcp` partagent l'hôte."""
    return IntegrationsDto(
        mcp_url=f"{settings.public_url.rstrip('/')}/mcp",
        protocol_versions=list(VERSIONS),
        oauth=OAuthDto(enabled=False),
        version=__version__,
    )

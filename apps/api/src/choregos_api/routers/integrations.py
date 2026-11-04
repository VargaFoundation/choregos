# SPDX-License-Identifier: Apache-2.0
"""Where to connect an MCP client to this platform (ADR 0030): what the Integrations page shows."""

from __future__ import annotations

from choregos_core.mcp import VERSIONS
from fastapi import APIRouter

from .. import __version__
from ..deps import Config, Me
from ..mcp.oauth import issuer_of
from ..schemas import IntegrationsDto, OAuthClientDto, OAuthDto

router = APIRouter(tags=["session"])


@router.get("/integrations", response_model=IntegrationsDto, operation_id="getIntegrations")
async def integrations(settings: Config, principal: Me) -> IntegrationsDto:
    """The door URL is derived from the public URL: the console and `/mcp` share the host.

    When the door accepts OAuth, the page says where to sign in and with which client: before, `oauth`
    was `enabled: false` whatever the deployment said, and the page only offered tokens."""
    oauth = OAuthDto(enabled=False)
    if settings.mcp_oauth_enabled:
        oauth = OAuthDto(
            enabled=True,
            authorization_server=issuer_of(settings),
            clients={
                name: OAuthClientDto(client_id=client.client_id, callback_port=client.callback_port)
                for name, client in sorted(settings.mcp_oauth_clients.items())
            },
        )
    return IntegrationsDto(
        mcp_url=f"{settings.public_url.rstrip('/')}/mcp",
        protocol_versions=list(VERSIONS),
        oauth=oauth,
        version=__version__,
    )

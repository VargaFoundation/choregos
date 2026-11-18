# SPDX-License-Identifier: Apache-2.0
"""Where to connect an MCP client to this platform (ADR 0030): what the Integrations page shows."""

from __future__ import annotations

from fastapi import APIRouter, Request
from tranibor_core.mcp import VERSIONS

from .. import __version__
from ..deps import Config, Me
from ..mcp.oauth import issuer_of
from ..schemas import IntegrationsDto, OAuthClientDto, OAuthDto
from ..tenant_host import request_base_url

router = APIRouter(tags=["session"])


@router.get("/integrations", response_model=IntegrationsDto, operation_id="getIntegrations")
async def integrations(request: Request, settings: Config, principal: Me) -> IntegrationsDto:
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
        mcp_url=f"{await request_base_url(request.headers, settings)}/mcp",
        protocol_versions=list(VERSIONS),
        oauth=oauth,
        version=__version__,
    )

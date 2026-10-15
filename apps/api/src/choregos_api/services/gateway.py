# SPDX-License-Identifier: Apache-2.0
"""The gateway of an organisation (ADR 0060): its own connector of kind `gateway`, else the deployment's."""

from __future__ import annotations

from typing import Any

from choregos_adapters import build, configuration_resolue
from choregos_adapters.base import GatewayAdapter
from choregos_adapters.registry import default_gateway_type
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import OrgConnector


async def org_gateway(session: AsyncSession, org_id: str) -> GatewayAdapter:
    """The organisation's gateway adapter (see `org_gateway_and_type`)."""
    return (await org_gateway_and_type(session, org_id))[1]


async def org_gateway_and_type(session: AsyncSession, org_id: str) -> tuple[str, GatewayAdapter]:
    """The gateway whose models the organisation's catalogue describes.

    An organisation connector of kind `gateway` wins (an organisation that brings its own
    gateway); otherwise the deployment's default (`CHOREGOS_GATEWAY_TYPE`, LiteLLM when unset).
    """
    connector = (
        (
            await session.execute(
                select(OrgConnector)
                .where(OrgConnector.org_id == org_id, OrgConnector.kind == "gateway")
                .order_by(OrgConnector.name)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    gateway: Any
    if connector is None:
        kind = default_gateway_type()
        gateway = build("gateway", kind, {})
    else:
        kind = connector.type
        config = configuration_resolue(
            connector.kind, connector.type, connector.config, connector.secret_refs
        )
        gateway = build("gateway", kind, config)
    return kind, gateway

# SPDX-License-Identifier: Apache-2.0
"""The connector catalogue: every connector type the platform knows, by family (ADR 0074).

Read-only. Installing an organisation connector, configuring a project's, turning one on for a
project each keep their own route; this one says what exists and, as the server decided, what the
caller may do with it.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from ..deps import Db, Me
from ..errors import forbidden
from ..schemas import ConnectorCatalogueDto
from ..services.catalogue_rights import CatalogueAction, decide
from ..services.connector_catalogue import list_entries
from .org_connectors import _organisation

router = APIRouter(tags=["connectors"])


@router.get(
    "/orgs/{org}/connector-catalogue",
    response_model=ConnectorCatalogueDto,
    operation_id="listConnectorCatalogue",
)
async def list_connector_catalogue(
    org: str,
    session: Db,
    principal: Me,
    q: Annotated[str | None, Query(max_length=200)] = None,
    family: Annotated[str | None, Query(max_length=40)] = None,
    scope: Annotated[Literal["organisation", "project"] | None, Query()] = None,
) -> ConnectorCatalogueDto:
    """The registry's connector types and what each means in this organisation. Its members can read it."""
    decision = decide(principal, org, CatalogueAction.SEE)
    if not decision.allowed:
        raise forbidden(decision.reason or "refused")
    organisation = await _organisation(session, org)
    entries = await list_entries(session, principal, organisation, q=q or "", family=family, scope=scope)
    return ConnectorCatalogueDto(entries=entries)

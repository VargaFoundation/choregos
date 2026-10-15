# SPDX-License-Identifier: Apache-2.0
"""The catalogue of well-known MCP servers, and installing one as a connector (ADR 0065).

An entry is an offer. Listing enables nothing; installing declares an ordinary `mcp` connector of
the organisation through the one door connectors already use, so everything that governs a
connector holds: only the organisation's administrator, the secret by reference, the tools born
closed on the first discovery, the groups, the audit row.
"""

from __future__ import annotations

from typing import Annotated, Literal

from choregos_core import mcp_catalogue as catalogue
from choregos_core.catalogues import CatalogueError
from fastapi import APIRouter, Path, Query, status
from sqlalchemy import select

from ..config import get_settings
from ..db.models import OrgConnector
from ..deps import Db, Me
from ..errors import forbidden, not_found, unprocessable
from ..rbac import Permission
from ..schemas import (
    CatalogueMetaDto,
    McpCatalogueDto,
    McpCatalogueEntryDto,
    McpCatalogueInstall,
    OrgConnectorCreate,
    OrgConnectorDto,
)
from .org_connectors import _administrer, _organisation, declare_org_connector

router = APIRouter(tags=["connectors"])

EntryId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]

Egress = Literal["open", "undeclared", "unknown"]


def _site_path() -> str | None:
    return get_settings().mcp_catalogue_extra or None


def _catalogue() -> catalogue.McpCatalogue:
    try:
        return catalogue.load_with_site(_site_path())
    except CatalogueError as refusal:
        raise unprocessable(f"the site MCP catalogue is invalid: {refusal}") from refusal


def host_open(host: str, declared: list[str]) -> bool:
    """`mcp.notion.com` is open when the deployment declares it, or a `*.notion.com` wildcard."""
    host = host.lower()
    for pattern in (d.lower().strip() for d in declared):
        if pattern == host or (pattern.startswith("*.") and host.endswith(pattern[1:])):
            return True
    return False


def egress_of(entry: catalogue.McpEntry, declared: list[str]) -> Egress:
    """Whether the deployment says the API can reach the entry's host.

    `unknown` when the deployment declares nothing (the common case: the network policy is not the
    API's to describe) or the entry has no host to reach. `undeclared` is a warning, not a verdict:
    the network policy decides, this only tells the administrator before a silent timeout."""
    if not declared or not entry.egress_hosts:
        return "unknown"
    return "open" if all(host_open(h, declared) for h in entry.egress_hosts) else "undeclared"


def _version_of(connector_entry: str) -> tuple[str, str]:
    entry_id, _, version = connector_entry.partition("@")
    return entry_id, version


def _dto(
    entry: catalogue.McpEntry,
    version: str,
    installed: dict[str, list[tuple[str, str]]],
    declared: list[str],
) -> McpCatalogueEntryDto:
    here = installed.get(entry.id, [])
    state: Literal["available", "installed", "update"] = "available"
    if here:
        state = "update" if any(v != version for _, v in here) else "installed"
    body = entry.model_dump(mode="json")
    body.update(
        origin=entry.origin,
        install=entry.install,
        state=state,
        installed_as=[name for name, _ in here],
        egress=egress_of(entry, declared),
    )
    return McpCatalogueEntryDto.model_validate(body)


async def _installed(session: Db, org_id: str) -> dict[str, list[tuple[str, str]]]:
    rows = (
        await session.execute(
            select(OrgConnector.name, OrgConnector.catalogue_entry).where(
                OrgConnector.org_id == org_id, OrgConnector.catalogue_entry.is_not(None)
            )
        )
    ).all()
    out: dict[str, list[tuple[str, str]]] = {}
    for name, catalogue_entry in rows:
        entry_id, version = _version_of(str(catalogue_entry))
        out.setdefault(entry_id, []).append((name, version))
    return out


def _matches(entry: catalogue.McpEntry, q: str) -> bool:
    needle = q.lower().strip()
    return (
        not needle or needle in " ".join([entry.id, entry.title, entry.publisher, entry.description]).lower()
    )


@router.get("/orgs/{org}/mcp-catalogue", response_model=McpCatalogueDto, operation_id="listMcpCatalogue")
async def list_mcp_catalogue(
    org: str,
    session: Db,
    principal: Me,
    q: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=40)] = None,
    install: Annotated[Literal["installable", "needs_oauth", "needs_hosting"] | None, Query()] = None,
    official: Annotated[bool | None, Query()] = None,
) -> McpCatalogueDto:
    """The shipped catalogue and what each entry means in this organisation. Its members can read it."""
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"the MCP catalogue is readable by the members of {org}")
    organisation = await _organisation(session, org)
    loaded = _catalogue()
    declared = get_settings().api_egress_hosts
    installed = await _installed(session, organisation.id)
    version = loaded.meta.catalogue_version
    entries = [
        _dto(e, version, installed, declared)
        for e in loaded.entries
        if _matches(e, q or "")
        and (category is None or category in e.categories)
        and (install is None or e.install == install)
        and (official is None or e.official is official)
    ]
    return McpCatalogueDto(meta=CatalogueMetaDto.model_validate(loaded.meta.model_dump()), entries=entries)


def _refusal_for(entry: catalogue.McpEntry) -> str | None:
    if entry.install == "needs_oauth":
        return (
            f"`{entry.id}` authenticates each user with OAuth, which an organisation connector cannot "
            "carry yet: it is listed, not installable"
        )
    if entry.install == "needs_hosting":
        package = entry.hosting.package if entry.hosting else "a local process"
        return (
            f"`{entry.id}` runs as a local process ({package}): hosting a third party's code is the "
            "operator's decision, not an install button"
        )
    return None


@router.post(
    "/orgs/{org}/mcp-catalogue/{entry_id}/install",
    response_model=OrgConnectorDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="installMcpCatalogueEntry",
)
async def install_mcp_catalogue_entry(
    org: str, entry_id: EntryId, body: McpCatalogueInstall, session: Db, principal: Me
) -> OrgConnectorDto:
    """Declares an `mcp` connector from the entry. Its tools are born closed on the first discovery."""
    _administrer(principal, org)
    organisation = await _organisation(session, org)
    loaded = _catalogue()
    entry = loaded.by_id(entry_id)
    if entry is None:
        raise not_found("MCP catalogue entry", entry_id)
    if refusal := _refusal_for(entry):
        raise unprocessable(refusal)
    assert entry.remote is not None  # `installable` means a hosted remote
    config: dict[str, object] = {"url": entry.remote.url}
    secret_refs = dict(body.secret_refs)
    if entry.auth == "none":
        if secret_refs:
            raise unprocessable(f"`{entry_id}` takes no credential: `secret_refs` must be empty")
    else:
        if "token" not in secret_refs:
            raise unprocessable(
                f"`{entry_id}` needs a credential: give a reference in `secret_refs.token` "
                "(a reference, never the value)"
            )
        header = entry.auth_header or body.auth_header
        if header and header.lower() != "authorization":
            config["auth_header"] = header
    declaration = OrgConnectorCreate(
        name=body.name or entry.id, type="mcp", kind="mcp", config=config, secret_refs=secret_refs
    )
    return await declare_org_connector(
        session,
        principal,
        organisation,
        declaration,
        catalogue_entry=f"{entry.id}@{loaded.meta.catalogue_version}",
    )

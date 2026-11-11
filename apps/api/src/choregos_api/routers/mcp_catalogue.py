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
from choregos_core.procedures import load as load_procedures
from choregos_core.procedures import procedure_of_mcp_entry
from fastapi import APIRouter, Path, Query, status
from sqlalchemy import select

from ..config import get_settings
from ..db.models import OrgConnector
from ..deps import Db, Me
from ..errors import conflict, forbidden, not_found, unprocessable
from ..schemas import (
    CatalogueMetaDto,
    CataloguePermissions,
    ConnectorEnablement,
    McpCatalogueDto,
    McpCatalogueEntryDto,
    McpCatalogueInstall,
    McpCatalogueUserConsent,
    OrgConnectorCreate,
    OrgConnectorDto,
)
from ..secret_store import NO_KEY_MESSAGE, derived_org_name, get_secret_store, store_ref
from ..services.catalogue_rights import (
    SELECTED_PROJECTS,
    CatalogueAction,
    decide,
    projects_where,
    visible_projects,
)
from ..services.catalogues import egress_of
from ..services.connector_catalogue import enablement_by_connector
from ..services.mcp_gateway import GATEWAY_TYPE, stored
from ..services.org_connector_access import require_connector_admin
from ..services.organisations import organisation_by_slug
from .org_connectors import declare_org_connector

router = APIRouter(tags=["connectors"])

EntryId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]


def _site_path() -> str | None:
    return get_settings().mcp_catalogue_extra or None


def _catalogue() -> catalogue.McpCatalogue:
    try:
        return catalogue.load_with_site(_site_path())
    except CatalogueError as refusal:
        raise unprocessable(f"the site MCP catalogue is invalid: {refusal}") from refusal


def _version_of(connector_entry: str) -> tuple[str, str]:
    entry_id, _, version = connector_entry.partition("@")
    return entry_id, version


def install_routes(entry: catalogue.McpEntry, gateway_mode: str | None) -> list[str]:
    """How this organisation can install the entry, `direct` first."""
    routes: list[str] = []
    if entry.install == "installable" and gateway_mode != "required":
        routes.append("direct")
    if gateway_mode in ("available", "required"):
        routes.append("gateway")
    return routes


def _dto(  # noqa: PLR0913
    entry: catalogue.McpEntry,
    version: str,
    installed: dict[str, list[tuple[str, str]]],
    declared: list[str],
    *,
    can_install: bool,
    install_reason: str | None,
    routes: list[str],
    enable_in: list[str],
    enablement: dict[str, ConnectorEnablement],
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
        egress=egress_of(entry.egress_hosts, declared),
        install_routes=routes,
        procedure="connector.mcp.mcp-gateway"
        if routes == ["gateway"]
        else procedure_of_mcp_entry(load_procedures(get_settings().procedures_extra), entry.id),
    )
    here_enablement = [enablement[name] for name, _ in here if name in enablement]
    selected = any(e.availability == SELECTED_PROJECTS for e in here_enablement)
    body["can"] = CataloguePermissions(
        install=can_install and bool(routes),
        configure_projects=[],
        enable_projects=enable_in if selected else [],
    ).model_dump()
    body["cannot_reason"] = install_reason or (None if "gateway" in routes else _refusal_for(entry))
    body["enablement"] = [e.model_dump() for e in here_enablement]
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
    seeing = decide(principal, org, CatalogueAction.SEE)
    if not seeing.allowed:
        raise forbidden(seeing.reason or "refused")
    organisation = await organisation_by_slug(session, org)
    loaded = _catalogue()
    declared = get_settings().api_egress_hosts
    installed = await _installed(session, organisation.id)
    version = loaded.meta.catalogue_version
    installing = decide(principal, org, CatalogueAction.INSTALL)
    visible = await visible_projects(session, principal, organisation.id, org)
    enable_in = projects_where(principal, org, CatalogueAction.ENABLE, visible)
    enablement = await enablement_by_connector(session, principal, organisation)
    mode = setting.mode if (setting := stored(organisation)) else None
    entries = [
        _dto(
            e,
            version,
            installed,
            declared,
            can_install=installing.allowed,
            install_reason=installing.reason,
            routes=install_routes(e, mode),
            enable_in=enable_in,
            enablement=enablement,
        )
        for e in loaded.entries
        if _matches(e, q or "")
        and (category is None or category in e.categories)
        and (install is None or e.install == install)
        and (official is None or e.official is official)
    ]
    return McpCatalogueDto(meta=CatalogueMetaDto.model_validate(loaded.meta.model_dump()), entries=entries)


def _consent_config(consent: McpCatalogueUserConsent, name: str) -> dict[str, object]:
    """The connector's `user_consent` declaration (S63-08): the sealed secret is named, never written."""
    settings: dict[str, object] = {
        "authorization_endpoint": consent.authorization_endpoint,
        "token_endpoint": consent.token_endpoint,
        "client_id": consent.client_id,
        "scopes": consent.scopes,
    }
    if consent.revocation_endpoint:
        settings["revocation_endpoint"] = consent.revocation_endpoint
    if consent.client_secret:
        settings["client_secret_ref"] = store_ref(derived_org_name(name, "oauth_client_secret"))
    return {"identity_modes": ["user_consent"], "user_consent": settings}


async def _seal_client_secret(
    session: Db, org_id: str, actor: str, name: str, consent: McpCatalogueUserConsent
) -> None:
    store = get_secret_store()
    if not store.available():
        raise conflict(NO_KEY_MESSAGE)
    assert consent.client_secret is not None
    await store.put(
        session, org_id, derived_org_name(name, "oauth_client_secret"), consent.client_secret, actor
    )


def _refuse(entry: catalogue.McpEntry) -> None:
    if refusal := _refusal_for(entry):
        raise unprocessable(refusal)


def _direct_config(
    entry: catalogue.McpEntry, body: McpCatalogueInstall
) -> tuple[dict[str, object], dict[str, str]]:
    """The connector's configuration and secret references for a direct install."""
    assert entry.remote is not None  # `installable` and `needs_oauth` mean a hosted remote
    config: dict[str, object] = {"url": entry.remote.url}
    secret_refs = dict(body.secret_refs)
    if body.user_consent is not None:
        if secret_refs or body.secret_values:
            raise unprocessable("with `user_consent` the server holds no credential of the organisation's")
        config.update(_consent_config(body.user_consent, body.name or entry.id))
    elif entry.auth == "none":
        if secret_refs or body.secret_values:
            raise unprocessable(f"`{entry.id}` takes no credential: `secret_refs` must be empty")
    else:
        if "token" not in secret_refs and "token" not in body.secret_values:
            raise unprocessable(
                f"`{entry.id}` needs a credential: give a reference in `secret_refs.token` "
                "or the value in `secret_values.token`"
            )
        header = entry.auth_header or body.auth_header
        if header and header.lower() != "authorization":
            config["auth_header"] = header
    return config, secret_refs


def _refusal_for(entry: catalogue.McpEntry) -> str | None:
    if entry.install == "needs_oauth":
        return (
            f"`{entry.id}` authenticates each user with OAuth, which an organisation connector's own "
            "credential cannot carry: install it with `user_consent` (each person connects their own account)"
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
    require_connector_admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    loaded = _catalogue()
    entry = loaded.by_id(entry_id)
    if entry is None:
        raise not_found("MCP catalogue entry", entry_id)
    setting = stored(organisation)
    mode = setting.mode if setting else None
    route = body.route
    consent = body.user_consent
    if consent is not None and (route == "gateway" or entry.install == "needs_hosting"):
        raise unprocessable(
            "`user_consent` calls the server with each person's own token: it needs a hosted remote, "
            "installed directly"
        )
    if route is None and consent is not None:
        route = "direct"
    if route is None:
        gateway_first = mode == "required" or (entry.install != "installable" and mode == "available")
        route = "gateway" if gateway_first else "direct"
    if route == "gateway":
        if body.secret_refs or body.secret_values or body.auth_header:
            raise unprocessable(
                "through the gateway the server's credential is the gateway's: Choregos stores none"
            )
        return await declare_org_connector(
            session,
            principal,
            organisation,
            OrgConnectorCreate(
                name=body.name or entry.id,
                type=GATEWAY_TYPE,
                kind="mcp",
                config={"server": body.gateway_server or entry.id},
                secret_refs={},
                secret_values={},
                availability=body.availability,
                allow_egress=body.allow_egress,
            ),
            catalogue_entry=f"{entry.id}@{loaded.meta.catalogue_version}",
        )
    if consent is None or entry.install != "needs_oauth":  # ADR 0135 point 6 lifts `needs_oauth`
        _refuse(entry)
    config, secret_refs = _direct_config(entry, body)
    declaration = OrgConnectorCreate(
        name=body.name or entry.id,
        type="mcp",
        kind="mcp",
        config=config,
        secret_refs=secret_refs,
        secret_values=body.secret_values,
        availability=body.availability,
        allow_egress=body.allow_egress,
    )
    declared = await declare_org_connector(
        session,
        principal,
        organisation,
        declaration,
        catalogue_entry=f"{entry.id}@{loaded.meta.catalogue_version}",
    )
    if consent is not None and consent.client_secret:
        await _seal_client_secret(session, organisation.id, principal.email, declaration.name, consent)
    return declared

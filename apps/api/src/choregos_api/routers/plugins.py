# SPDX-License-Identifier: Apache-2.0
"""The plugins of an organisation (ADR 0093, S43-04, S43-05): list, install, upgrade, uninstall, and the
second approval a plugin that brings a server waits for.

Any member reads the list and the requests waiting; installing, upgrading, uninstalling and approving are
the organisation administrator's (ADR 0074, `CatalogueAction.INSTALL`). The plugin itself comes from the
deployment's `PluginSupply` (`services/plugin_supply.py`): its sources and their trust (S43-03).

A plugin that brings a server is not installed on one administrator's word (ADR 0050): the install or
upgrade is checked in full, recorded, and answered 202 `pending_approval`; it happens when ANOTHER
administrator approves (`POST /orgs/{org}/plugin-approvals/{id}/approve`), and only if the source still
serves the content that was checked.
"""

from __future__ import annotations

from typing import Annotated

from choregos_core.plugins import Plugin, PluginError
from fastapi import APIRouter, Path, status
from fastapi.responses import JSONResponse

from ..db.models import OrgPluginApproval
from ..deps import Config, Db, Me
from ..errors import ApiError, conflict, forbidden, not_found, unprocessable
from ..schemas.plugins import (
    PLUGIN_NAME,
    InstalledPlugin,
    PluginApprovalDto,
    PluginApprovalList,
    PluginInstall,
    PluginInstallPending,
    PluginKept,
    PluginList,
    PluginUninstallResult,
    PluginUpgrade,
    PluginUpgradeResult,
)
from ..services import plugin_approvals as approvals
from ..services import plugin_installer as installer
from ..services.catalogue_rights import CatalogueAction, decide
from ..services.plugin_sources import assess_plugin
from ..services.plugin_supply import (
    PluginNotFoundError,
    PluginVersionUnsupportedError,
    SourceError,
    plugin_supply,
)

router = APIRouter(tags=["agents"])

Name = Annotated[str, Path(pattern=PLUGIN_NAME)]
ApprovalId = Annotated[str, Path(min_length=1, max_length=64)]
SUPPLY_ERRORS = (PluginNotFoundError, PluginVersionUnsupportedError, PluginError, SourceError)
PENDING = {
    status.HTTP_202_ACCEPTED: {"model": PluginInstallPending, "description": "Waiting for a second approval"}
}


def _may_see(principal: Me, org: str) -> None:
    if not decide(principal, org, CatalogueAction.SEE).allowed:
        raise forbidden(f"the plugins of {org} are visible to its members")


def _may_install(principal: Me, org: str) -> None:
    if not decide(principal, org, CatalogueAction.INSTALL).allowed:
        raise forbidden(f"plugins are installed, upgraded and uninstalled by the administrator of {org}")


def _supply_error(error: Exception) -> ApiError:
    if isinstance(error, PluginNotFoundError):
        return not_found("Plugin", str(error))
    if isinstance(error, SourceError):
        if error.check in {"not_found", "version"}:
            return not_found("Plugin", f"{error.check}: {error.message}")
        # The contract: a refusal of the gate is a 422 naming the check.
        return unprocessable(
            f"the plugin is refused by the `{error.check}` check",
            [{"loc": ["plugin", error.check], "msg": error.message}],
        )
    if isinstance(error, PluginError):
        return unprocessable(
            "the plugin is not valid", [{"loc": ["plugin"], "msg": p} for p in error.problems]
        )
    return unprocessable(str(error))


def _pending(row: OrgPluginApproval) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=approvals.pending_body(row))


@router.get("/orgs/{org}/plugins", response_model=PluginList, operation_id="listPlugins")
async def list_plugins(org: str, session: Db, principal: Me, settings: Config) -> PluginList:
    _may_see(principal, org)
    org_id = await installer.org_id_of(session, org)
    supply = plugin_supply(settings)
    return PluginList(
        items=[await installer.to_dto(r, supply) for r in await installer.installed(session, org_id)]
    )


@router.post(
    "/orgs/{org}/plugins/install",
    response_model=InstalledPlugin,
    status_code=status.HTTP_201_CREATED,
    operation_id="installPlugin",
    responses=PENDING,  # type: ignore[arg-type]
)
async def install_plugin(
    org: str, body: PluginInstall, session: Db, principal: Me, settings: Config
) -> InstalledPlugin | JSONResponse:
    _may_install(principal, org)
    org_id = await installer.org_id_of(session, org)
    supply = plugin_supply(settings)
    try:
        async with supply.obtain(body.source, body.name, body.version) as plugin:
            installed = await installer.installed_row(session, org_id, plugin.name)
            if installed is not None:
                if installed.digest == plugin.digest:
                    raise conflict(f"`{plugin.name}` {installed.version} is already installed in {org}")
                raise conflict(
                    f"`{plugin.name}` {installed.version} is installed: upgrade it to {plugin.version}"
                )
            gate = assess_plugin(plugin)
            if gate.needs_second_approval:
                await installer.check(session, org_id, plugin, settings, replacing=None)
                row, _ = await approvals.request(
                    session, org_id, approvals.INSTALL, body.source, plugin, gate.reasons, principal
                )
                return _pending(row)
            installed, _ = await installer.install(session, org_id, plugin, body.source, settings, principal)
    except SUPPLY_ERRORS as error:
        raise _supply_error(error) from error
    return await installer.to_dto(installed, supply)


@router.post(
    "/orgs/{org}/plugins/{name}/upgrade",
    response_model=PluginUpgradeResult,
    operation_id="upgradePlugin",
    responses=PENDING,  # type: ignore[arg-type]
)
async def upgrade_plugin(
    org: str, name: Name, session: Db, principal: Me, settings: Config, body: PluginUpgrade | None = None
) -> PluginUpgradeResult | JSONResponse:
    _may_install(principal, org)
    org_id = await installer.org_id_of(session, org)
    row = await installer.installed_one(session, org_id, name)
    supply = plugin_supply(settings)
    try:
        async with supply.obtain(row.source, name, body.version if body else None) as plugin:
            gate = assess_plugin(plugin)
            if plugin.digest != row.digest and gate.needs_second_approval:
                if plugin.name != row.name:
                    raise unprocessable(f"the source served `{plugin.name}` for `{row.name}`")
                await installer.check(session, org_id, plugin, settings, replacing=row)
                request, _ = await approvals.request(
                    session, org_id, approvals.UPGRADE, row.source, plugin, gate.reasons, principal
                )
                return _pending(request)
            previous, offered = await installer.upgrade(session, org_id, row, plugin, settings, principal)
    except SUPPLY_ERRORS as error:
        raise _supply_error(error) from error
    return PluginUpgradeResult(
        plugin=await installer.to_dto(row, supply), previous_version=previous, offered=offered
    )


@router.delete(
    "/orgs/{org}/plugins/{name}",
    response_model=PluginUninstallResult,
    operation_id="uninstallPlugin",
)
async def uninstall_plugin(
    org: str, name: Name, session: Db, principal: Me, settings: Config
) -> PluginUninstallResult:
    _may_install(principal, org)
    org_id = await installer.org_id_of(session, org)
    row = await installer.installed_one(session, org_id, name)
    version = row.version
    outcome = await installer.uninstall(session, org_id, row, principal, settings)
    await approvals.forget(session, org_id, name)
    return PluginUninstallResult(
        name=name,
        version=version,
        removed=PluginKept.model_validate(outcome["removed"]),
        kept=PluginKept.model_validate(outcome["kept"]),
    )


# ───────────────────────────── the second approval (ADR 0050) ─────────────────────────────


@router.get(
    "/orgs/{org}/plugin-approvals", response_model=PluginApprovalList, operation_id="listPluginApprovals"
)
async def list_plugin_approvals(org: str, session: Db, principal: Me) -> PluginApprovalList:
    _may_see(principal, org)
    org_id = await installer.org_id_of(session, org)
    return PluginApprovalList(items=[approvals.to_dto(r) for r in await approvals.listed(session, org_id)])


async def _approved_plugin(
    session: Db, org_id: str, request: OrgPluginApproval, plugin: Plugin, settings: Config, principal: Me
) -> None:
    if plugin.digest != request.digest:
        raise conflict(
            f"the source now serves `{request.name}` {plugin.version} as {plugin.digest}, not the "
            f"{request.digest} that was checked: ask for the install again"
        )
    if request.action == approvals.INSTALL:
        if await installer.installed_row(session, org_id, request.name) is not None:
            raise conflict(f"`{request.name}` was installed meanwhile")
        await installer.install(
            session, org_id, plugin, request.source, settings, principal,
            default=request.is_default, installed_by=request.requested_by,
        )  # fmt: skip
        return
    row = await installer.installed_one(session, org_id, request.name)
    await installer.upgrade(session, org_id, row, plugin, settings, principal)


@router.post(
    "/orgs/{org}/plugin-approvals/{approval_id}/approve",
    response_model=PluginApprovalDto,
    operation_id="approvePluginInstall",
)
async def approve_plugin_install(
    org: str, approval_id: ApprovalId, session: Db, principal: Me, settings: Config
) -> PluginApprovalDto:
    """A second administrator approves: the plugin is obtained again at the exact version, and installed
    (or upgraded) only if its content is the one that was checked."""
    _may_install(principal, org)
    org_id = await installer.org_id_of(session, org)
    request = await approvals.pending_one(session, org_id, approval_id)
    approvals.check_second_person(request, principal)
    supply = plugin_supply(settings)
    try:
        async with supply.obtain(request.source, request.name, request.version) as plugin:
            await _approved_plugin(session, org_id, request, plugin, settings, principal)
    except SUPPLY_ERRORS as error:
        raise _supply_error(error) from error
    await approvals.mark_approved(session, request, principal)
    return approvals.to_dto(request)

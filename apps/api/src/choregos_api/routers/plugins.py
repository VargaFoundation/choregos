# SPDX-License-Identifier: Apache-2.0
"""The plugins of an organisation (ADR 0093, S43-04): list, install, upgrade, uninstall.

Any member reads the list; installing, upgrading and uninstalling are the organisation administrator's
(ADR 0074, `CatalogueAction.INSTALL`). The plugin itself comes from the deployment's `PluginSupply`
(`services/plugin_supply.py`); the sources and their trust are S43-03's.
"""

from __future__ import annotations

from typing import Annotated

from choregos_core.plugins import PluginError
from fastapi import APIRouter, Path, Response, status

from ..deps import Config, Db, Me
from ..errors import ApiError, conflict, forbidden, not_found, unprocessable
from ..schemas.plugins import (
    PLUGIN_NAME,
    InstalledPlugin,
    PluginInstall,
    PluginList,
    PluginUpgrade,
    PluginUpgradeResult,
)
from ..services import plugin_installer as installer
from ..services.catalogue_rights import CatalogueAction, decide
from ..services.plugin_supply import PluginNotFoundError, PluginVersionUnsupportedError, plugin_supply

router = APIRouter(tags=["agents"])

Name = Annotated[str, Path(pattern=PLUGIN_NAME)]


def _may_see(principal: Me, org: str) -> None:
    if not decide(principal, org, CatalogueAction.SEE).allowed:
        raise forbidden(f"the plugins of {org} are visible to its members")


def _may_install(principal: Me, org: str) -> None:
    if not decide(principal, org, CatalogueAction.INSTALL).allowed:
        raise forbidden(f"plugins are installed, upgraded and uninstalled by the administrator of {org}")


def _supply_error(error: Exception) -> ApiError:
    if isinstance(error, PluginNotFoundError):
        return not_found("Plugin", str(error))
    if isinstance(error, PluginError):
        return unprocessable(
            "the plugin is not valid", [{"loc": ["plugin"], "msg": p} for p in error.problems]
        )
    return unprocessable(str(error))


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
)
async def install_plugin(
    org: str, body: PluginInstall, session: Db, principal: Me, settings: Config
) -> InstalledPlugin:
    _may_install(principal, org)
    org_id = await installer.org_id_of(session, org)
    supply = plugin_supply(settings)
    try:
        async with supply.obtain(body.source, body.name, body.version) as plugin:
            row, changed = await installer.install(session, org_id, plugin, body.source, settings, principal)
    except (PluginNotFoundError, PluginVersionUnsupportedError, PluginError) as error:
        raise _supply_error(error) from error
    if not changed:
        raise conflict(f"`{body.name}` {row.version} is already installed in {org}")
    return await installer.to_dto(row, supply)


@router.post(
    "/orgs/{org}/plugins/{name}/upgrade",
    response_model=PluginUpgradeResult,
    operation_id="upgradePlugin",
)
async def upgrade_plugin(
    org: str, name: Name, session: Db, principal: Me, settings: Config, body: PluginUpgrade | None = None
) -> PluginUpgradeResult:
    _may_install(principal, org)
    org_id = await installer.org_id_of(session, org)
    row = await installer.installed_one(session, org_id, name)
    supply = plugin_supply(settings)
    try:
        async with supply.obtain(row.source, name, body.version if body else None) as plugin:
            previous, offered = await installer.upgrade(session, org_id, row, plugin, settings, principal)
    except (PluginNotFoundError, PluginVersionUnsupportedError, PluginError) as error:
        raise _supply_error(error) from error
    return PluginUpgradeResult(
        plugin=await installer.to_dto(row, supply), previous_version=previous, offered=offered
    )


@router.delete(
    "/orgs/{org}/plugins/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="uninstallPlugin",
)
async def uninstall_plugin(org: str, name: Name, session: Db, principal: Me) -> Response:
    _may_install(principal, org)
    org_id = await installer.org_id_of(session, org)
    row = await installer.installed_one(session, org_id, name)
    await installer.uninstall(session, org_id, row, principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

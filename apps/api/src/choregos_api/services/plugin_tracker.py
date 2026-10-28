# SPDX-License-Identifier: Apache-2.0
"""A plugin's server as a project's tracker (plan S43 Decision 3, S43-07).

A project connector `tracker` of type `plugin` holds `{plugin, server}` and nothing else. Whenever the
platform builds that adapter (the API, the orchestrator, the connector test, the webhook), it reads the
organisation's connector of the server (S43-06) and injects what the adapter calls it with: the Service
URL the platform wrote, the server's bearer token, the transport (fakes and tests) and an audit hook.
Those values are never typed and never stored on the project connector.

A template whose `requires.connectors.tracker` is `plugin:<plugin>/<server>` wires that connector when a
project is created from it, and refuses when the plugin is not installed in the organisation, the server
is not its tracker, or the server is not running (`deployed`).
"""

from __future__ import annotations

from typing import Any

from choregos_core.plugins import PLUGIN_TRACKER
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Connector, OrgConnector, OrgPlugin, Project
from ..errors import unprocessable
from ..logging import get_logger
from . import plugin_servers

log = get_logger(__name__)

KIND, TYPE = "tracker", "plugin"


async def server_connector(
    session: AsyncSession, org_id: str, plugin: str, server: str
) -> OrgConnector | None:
    """The organisation's connector of `plugin`'s server `server`, if the plugin brought it."""
    found = (
        await session.execute(
            select(OrgConnector).where(OrgConnector.org_id == org_id, OrgConnector.name == server)
        )
    ).scalar_one_or_none()
    declared = (found.plugin_server or {}) if found is not None else {}
    if declared.get("plugin") != plugin or declared.get("server") != server:
        return None
    return found


async def tracker_problem(session: AsyncSession, org_id: str, plugin: str, server: str) -> str | None:
    """Why `plugin:<plugin>/<server>` cannot be this organisation's tracker now, or `None`."""
    installed = (
        await session.execute(select(OrgPlugin).where(OrgPlugin.org_id == org_id, OrgPlugin.name == plugin))
    ).scalar_one_or_none()
    if installed is None:
        return f"the plugin `{plugin}` is not installed in this organisation"
    found = await server_connector(session, org_id, plugin, server)
    if found is None:
        return f"the plugin `{plugin}` runs no server `{server}` in this organisation"
    if "tracker" not in ((found.plugin_server or {}).get("implements") or []):
        return f"the server `{server}` of `{plugin}` does not implement the tracker profile"
    if found.status != plugin_servers.DEPLOYED:
        detail = f": {found.last_error}" if found.last_error else ""
        state = f"`{found.status}`{detail}"
        return f"the server `{server}` of `{plugin}` is not running ({state}); configure it first"
    return None


def _auditor(org_id: str, connector_id: str) -> Any:
    async def audit(tool: str, details: dict[str, Any]) -> None:
        # Its own transaction: the write happened in the server's system whatever the caller's
        # transaction does next, so its trace must not roll back with it.
        from ..db.session import session_scope

        async with session_scope(orgs="*") as session:
            await record(
                session,
                None,
                f"tracker.plugin.{tool}",
                org_id=org_id,
                target_type="org_connector",
                target_id=connector_id,
                **details,
            )

    return audit


async def pin_plugin_tracker(
    session: AsyncSession, org_id: str, kind: str, type_: str, config: dict[str, Any]
) -> dict[str, Any]:
    """The configuration a `tracker/plugin` adapter is built with: `{plugin, server}` plus the URL, the
    token, the transport and the audit hook read from the organisation's connector of the server. Any
    other connector's configuration is returned as is. Without a usable server connector the URL and
    token are absent and the adapter's build fails, naming the plugin and the server."""
    if (kind, type_) != (KIND, TYPE):
        return config
    clean: dict[str, Any] = {k: config[k] for k in ("plugin", "server") if k in config}
    found = await server_connector(session, org_id, str(clean.get("plugin")), str(clean.get("server")))
    if found is None:
        return clean
    endpoint = await plugin_servers.server_endpoint(session, found)
    if endpoint is None:
        return clean
    return {
        **clean,
        "url": endpoint[0],
        "token": endpoint[1],
        "transport": plugin_servers.transport(),
        "audit": _auditor(org_id, found.id),
    }


async def wire_template_tracker(session: AsyncSession, project: Project) -> None:
    """A project born from a template whose tracker is `plugin:<plugin>/<server>` gets its tracker
    connector `{type: plugin, config: {plugin, server}}` — or the creation is refused (422)."""
    from .gabarits import manifeste_du_gabarit

    if not project.template_ref:
        return
    manifest, _ = await manifeste_du_gabarit(session, project.template_ref, project.org_id)
    wanted = ((manifest.get("requires") or {}).get("connectors") or {}).get("tracker")
    if not isinstance(wanted, str) or not wanted.startswith(PLUGIN_TRACKER):
        return
    plugin, _, server = wanted.removeprefix(PLUGIN_TRACKER).partition("/")
    problem = await tracker_problem(session, project.org_id, plugin, server)
    if problem is not None:
        raise unprocessable(
            f"the template `{project.template_ref}` takes its tracker from `{wanted}`: {problem}",
            [{"loc": ["template_ref"], "msg": wanted}],
        )
    session.add(
        Connector(
            project_id=project.id,
            kind=KIND,
            type=TYPE,
            config={"plugin": plugin, "server": server},
            status="unknown",
        )
    )
    await session.flush()


__all__ = ["pin_plugin_tracker", "server_connector", "tracker_problem", "wire_template_tracker"]

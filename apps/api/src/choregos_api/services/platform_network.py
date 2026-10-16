# SPDX-License-Identifier: Apache-2.0
"""What the platform itself reaches on an agent's behalf obeys the organisation's network policy
(ADR 0070): MCP connectors, knowledge sources, git skill sources.

A run's own traffic goes through the egress proxy; these calls are made by the API, which sees the
whole request, so it is the API that holds the wall:

- at **declaration** (create or update of a connector, a knowledge source, a skill import) a host the
  organisation's ceiling refuses is a 422 that names the host and where to change it;
- at **call** time (the broker, a governed write, discovery, a knowledge refresh) the same rule is
  read again: the ceiling may have been tightened after the declaration. A refusal is a 403
  `network_policy`, and, when the call belongs to a project, a `network_decisions` row (layer
  `broker`) the administrator reads under Network activity;
- **politeness**: the ceiling's `per_host_per_minute` caps the calls the organisation makes to one
  host, counted from the cost ledger (`kind='tool'`), so that every replica sees the same number;
- every request carries an identifying `User-Agent` with the organisation's contact.

Hosts that are not on the internet (an IP literal, a single-label name such as `pfsense`) are not
subject to the allowed set: the ceiling bounds what an agent may do on the internet, and an
organisation's own appliances are reached by address. The deny list still applies by name.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from choregos_core import utcnow
from choregos_core.network import allowed, normalise_host
from choregos_core.policy_ceiling import CeilingNetwork
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__
from ..config import get_settings
from ..db.models import CostLedger, NetworkDecision, Organization, OrgConnector, Project
from ..errors import ApiError, unprocessable
from .definitions import ceiling_of
from .network import instance_deny

DEFAULT_CONTACT = "https://github.com/VargaFoundation/choregos"
#: Configuration keys that hold the address a connector reaches, in the order they are read.
URL_KEYS = ("url", "base_url", "api_url", "endpoint", "server_url", "host")
WHERE = "change the organisation's network access on the Administration page, or the connector's address"


class PlatformNetworkRefused(Exception):  # noqa: N818 - a motivated refusal, rendered as 403 or 422
    def __init__(self, host: str, reason: str, purpose: str) -> None:
        self.host, self.reason, self.purpose = host, reason, purpose
        super().__init__(self.message())

    def message(self) -> str:
        return (
            f"{self.purpose}: `{self.host}` is refused by the organisation's network policy ({self.reason})"
        )


def host_of(url: str | None) -> str | None:
    """The host name of an address (`https://x.example/mcp` or a bare `x.example:8080`), or `None`."""
    if not url or not isinstance(url, str):
        return None
    text = url.strip()
    parts = urlsplit(text if "://" in text else f"//{text}")
    host = parts.hostname
    return normalise_host(host) if host else None


def connector_host(config: Mapping[str, Any] | None) -> str | None:
    """The host a connector's configuration points at, when it points at one."""
    for key in URL_KEYS:
        host = host_of((config or {}).get(key))
        if host:
            return host
    return None


def _is_internal(host: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return "." not in host
    return True


def decide(host: str, net: CeilingNetwork | None) -> tuple[bool, str]:
    """Whether the platform may reach `host` for this organisation, and why."""
    name = normalise_host(host)
    deny = [*(net.deny_domains if net else []), *instance_deny()]
    if _is_internal(name):
        # Internal: only a deny entry that names it (a single label) can refuse it.
        return (False, "denied") if any(name == p for p in deny) else (True, "internal")
    cap = ["*"] if net is None or net.allow_domains is None else net.allow_domains
    return allowed(name, cap, deny)


async def ceiling_network(session: AsyncSession, org_id: str) -> CeilingNetwork | None:
    ceiling = ceiling_of(await session.get(Organization, org_id))
    return ceiling.network if ceiling else None


async def check_platform_host(
    session: AsyncSession, org_id: str, url: str | None, purpose: str
) -> str | None:
    """At declaration: 422 when the organisation's policy refuses the address's host. Returns the
    host (or `None` when there is none to check: a connector that does not reach an address)."""
    host = host_of(url)
    if host is None:
        return None
    ok, reason = decide(host, await ceiling_network(session, org_id))
    if not ok:
        raise unprocessable(
            f"{PlatformNetworkRefused(host, reason, purpose).message()}: {WHERE}",
            [{"loc": ["body", "config"], "msg": f"`{host}`: {reason}"}],
        )
    return host


async def check_connector_declaration(
    session: AsyncSession, org_id: str, config: Mapping[str, Any] | None, name: str
) -> None:
    await check_platform_host(session, org_id, connector_host(config), f"connector `{name}`")


def refusal_error(refused: PlatformNetworkRefused) -> ApiError:
    return ApiError(
        403,
        "Network policy",
        f"{refused.message()}: {WHERE}",
        type_="network_policy",
    )


async def journal(
    session: AsyncSession,
    project: Project | None,
    host: str,
    decision: str,
    reason: str,
    layer: str,
    *,
    run_id: str | None = None,
    agent_slug: str | None = None,
) -> None:
    """One `network_decisions` row per (run, layer, host, decision, reason), counted up. Without a
    project (an organisation-level knowledge source) there is no row: the audit log carries it."""
    if project is None:
        return
    now = utcnow()
    row = (
        await session.execute(
            select(NetworkDecision).where(
                NetworkDecision.run_id == run_id if run_id else NetworkDecision.run_id.is_(None),
                NetworkDecision.project_id == project.id,
                NetworkDecision.layer == layer,
                NetworkDecision.host == host,
                NetworkDecision.port == 0,
                NetworkDecision.decision == decision,
                NetworkDecision.reason == reason[:200],
            )
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(
            NetworkDecision(
                id=str(uuid.uuid4()),
                org_id=project.org_id,
                project_id=project.id,
                run_id=run_id,
                agent_slug=agent_slug,
                layer=layer,
                host=host,
                port=0,
                decision=decision,
                reason=reason[:200],
                count=1,
                first_at=now,
                last_at=now,
            )
        )
    else:
        row.count += 1
        row.last_at = now
    await session.flush()


async def enforce_connector_call(
    session: AsyncSession,
    instance: OrgConnector,
    *,
    project: Project | None = None,
    run_id: str | None = None,
    agent_slug: str | None = None,
    polite: bool = True,
    now: datetime | None = None,
) -> None:
    """At call: raise `PlatformNetworkRefused` (403) or `PlatformRateLimited` (429) before the
    connector's server is reached. A connector with no address is not checked."""
    host = connector_host(instance.config)
    if host is None:
        return
    net = await ceiling_network(session, instance.org_id)
    ok, reason = decide(host, net)
    if not ok:
        await journal(
            session, project, host, "denied", reason, "broker", run_id=run_id, agent_slug=agent_slug
        )
        raise PlatformNetworkRefused(host, reason, f"connector `{instance.name}`")
    cap = net.per_host_per_minute if net else None
    if polite and cap:
        used = await calls_to_host(session, instance.org_id, host, now=now)
        if used >= cap:
            await journal(
                session, project, host, "rate_limited", f"per_host_per_minute:{cap}", "broker",
                run_id=run_id, agent_slug=agent_slug,
            )  # fmt: skip
            raise PlatformRateLimited(host, cap)


class PlatformRateLimited(Exception):  # noqa: N818 - a motivated refusal, rendered as 429
    def __init__(self, host: str, cap: int) -> None:
        self.host, self.cap = host, cap
        super().__init__(
            f"the organisation already made {cap} calls to `{host}` in the last minute "
            "(network access: requests per minute per host): retry shortly"
        )


def rate_limited_error(limited: PlatformRateLimited) -> ApiError:
    return ApiError(429, "Too many requests to one host", str(limited), retry_after=60)


async def calls_to_host(session: AsyncSession, org_id: str, host: str, *, now: datetime | None = None) -> int:
    """The tool calls of the organisation that reached `host` in the last 60 seconds: those of the
    connectors that point at it (`mcp:<name>`) and of `web_fetch` (`web:<host>`), from the ledger."""
    since = (now or utcnow()) - timedelta(seconds=60)
    connectors = (
        await session.execute(
            select(OrgConnector.name, OrgConnector.config).where(OrgConnector.org_id == org_id)
        )
    ).all()
    providers = [f"mcp:{name}" for name, config in connectors if connector_host(config) == host]
    providers.append(f"web:{host}")
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(CostLedger)
                .join(Project, Project.id == CostLedger.project_id)
                .where(
                    Project.org_id == org_id,
                    CostLedger.kind == "tool",
                    CostLedger.provider.in_(providers),
                    CostLedger.ts >= since,
                )
            )
        ).scalar_one()
    )


async def user_agent(session: AsyncSession, org_id: str) -> str:
    """`Choregos/<version> (+<contact>)`: the organisation's contact, else the deployment's."""
    net = await ceiling_network(session, org_id)
    contact = (net.user_agent_contact if net else None) or get_settings().user_agent_contact
    return f"Choregos/{__version__} (+{contact or DEFAULT_CONTACT})"


async def with_identity(session: AsyncSession, org_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """A resolved connector configuration that also carries the platform's `user_agent` (read by the
    MCP client; other connector types ignore it). To build an adapter, never to save."""
    return {**config, "user_agent": await user_agent(session, org_id)}


__all__ = [
    "PlatformNetworkRefused",
    "PlatformRateLimited",
    "calls_to_host",
    "check_connector_declaration",
    "check_platform_host",
    "connector_host",
    "decide",
    "enforce_connector_call",
    "host_of",
    "journal",
    "rate_limited_error",
    "refusal_error",
    "user_agent",
    "with_identity",
]

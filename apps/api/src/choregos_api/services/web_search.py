# SPDX-License-Identifier: Apache-2.0
"""`web_search`: the governed way an agent finds pages on the web (S40-01g, ADR 0083).

A search query is outbound text written by an agent that has read the work item, the repository and the
memories. What matters is what the search provider does with it. So this built-in tool:

- is offered only when the organisation's ceiling opens it, the project's policy does not close it, the
  organisation has a `web_search` connector turned on for the project, and that backend's declared data
  handling (retention, reuse, region; ADR 0083 §3) meets both — `choregos_core.web_search.shortfalls`;
- refuses a query that redaction would change (422 `query_redacted`): nothing is rewritten, nothing is
  sent;
- calls the backend from the API, its key resolved here, under the organisation's network ceiling and
  egress allowlist; the organisation's queries to one backend are counted per minute (429);
- returns links and snippets, never pages, each saying whether the run's network lets `web_fetch` read
  it (`fetchable`).

The router writes the ledger row (`search:<backend>`) and the event, which carries the query's
fingerprint and length, never its text (ADR 0015).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from choregos_core import utcnow
from choregos_core.network import allowed
from choregos_core.redaction import redact
from choregos_core.web_search import DataHandling, shortfalls, stricter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CostLedger, Organization, OrgConnector, Project, Run
from .catalogue_rights import connector_enabled_for
from .definitions import ceiling_of, effective_policy
from .network import run_network
from .platform_network import ceiling_network, decide, journal

TOOL_NAME = "web_search"
MAX_RESULTS = 10
MAX_QUERY_CHARS = 400

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_QUERY_CHARS,
            "description": "What to search for",
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_RESULTS,
            "description": "5 by default",
        },
        "site": {"type": "string", "description": "Only results from this host, e.g. docs.python.org"},
        "recency": {"type": "string", "enum": ["day", "week", "month", "year"]},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def announcement() -> dict[str, Any]:
    """The tool as the MCP sidecar serves it to the agent."""
    return {
        "name": TOOL_NAME,
        "description": (
            "Search the web through the platform: links and snippets, never pages. Read a result with "
            "web_fetch when it says `fetchable: true`. Do not put secrets, credentials or ticket "
            f"contents in a query: a query that holds one is refused. At most {MAX_RESULTS} results."
        ),
        "inputSchema": INPUT_SCHEMA,
    }


@dataclass(frozen=True)
class Backend:
    connector: OrgConnector
    handling: DataHandling

    @property
    def provider(self) -> str:
        return f"search:{self.connector.type}"


async def backend_for(session: AsyncSession, project: Project) -> tuple[Backend | None, list[str]]:
    """The backend this project's runs may search through, or why there is none."""
    organisation = await session.get(Organization, project.org_id)
    ceiling = ceiling_of(organisation)
    bound = ceiling.web_search if ceiling else None
    if bound is None or bound.mode != "allow":
        return None, ["the organisation's ceiling does not open web search"]
    policy = (await effective_policy(session, project.id)).web_search
    connectors = (
        (
            await session.execute(
                select(OrgConnector)
                .where(OrgConnector.org_id == project.org_id, OrgConnector.kind == TOOL_NAME)
                .order_by(OrgConnector.name)
            )
        )
        .scalars()
        .all()
    )
    reasons: list[str] = []
    for connector in connectors:
        if not await connector_enabled_for(session, connector, project):
            continue
        from choregos_adapters.web_search import declared

        try:
            handling = stricter(declared(connector.type), (connector.config or {}).get("data_handling"))
        except ValueError as wider:
            reasons.append(f"`{connector.name}`: {wider}")
            continue
        said = shortfalls(connector.type, handling, bound, policy)
        if not said:
            return Backend(connector, handling), []
        reasons.extend(f"`{connector.name}`: {reason}" for reason in said)
    return None, reasons or ["the organisation has no web_search connector for this project"]


async def offered(session: AsyncSession, project: Project) -> bool:
    backend, _ = await backend_for(session, project)
    return backend is not None


def fingerprint(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _refusal(code: int, kind: str, message: str, **extra: Any) -> tuple[int, dict[str, Any]]:
    return code, {"error": message, "type": kind, **extra}


def price_usd(backend: Backend) -> float:
    """What one query costs, as the organisation declared it on the connector (0 when it did not)."""
    try:
        return max(0.0, float((backend.connector.config or {}).get("price_usd_per_query") or 0.0))
    except (TypeError, ValueError):
        return 0.0


async def _calls_this_minute(session: AsyncSession, org_id: str, provider: str) -> int:
    since = utcnow() - timedelta(seconds=60)
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(CostLedger)
                .join(Project, Project.id == CostLedger.project_id)
                .where(
                    Project.org_id == org_id,
                    CostLedger.kind == "tool",
                    CostLedger.provider == provider,
                    CostLedger.ts >= since,
                )
            )
        ).scalar_one()
    )


async def search(
    session: AsyncSession, run: Run, project: Project, arguments: dict[str, Any]
) -> tuple[int, dict[str, Any], Backend | None]:
    """One search. Returns (status code, result, backend); the backend is `None` when none serves the
    project (a 404: the tool is not offered)."""
    backend, reasons = await backend_for(session, project)
    if backend is None:
        return (
            *_refusal(404, "not_offered", "web_search is not offered to this run: " + "; ".join(reasons)),
            None,
        )
    query = str(arguments["query"])
    if redact(query) != query:
        return (
            *_refusal(
                422,
                "query_redacted",
                "the query holds what looks like a secret or a credential: it was not sent. "
                "Search without it.",
            ),
            backend,
        )
    connector = backend.connector
    from choregos_adapters import build
    from choregos_adapters.errors import AdapterError
    from choregos_adapters.registry import egress_hosts
    from choregos_core.secrets import SecretIntrouvable

    from ..secret_store import resolved_configuration
    from .egress import egress_scope_for_org

    # The organisation's network bound holds for the backend's host as for any connector's.
    net = await ceiling_network(session, project.org_id)
    for host, _port in egress_hosts(connector.kind, connector.type, dict(connector.config or {})):
        ok, reason = decide(host, net)
        if not ok:
            await journal(
                session, project, host, "denied", reason, "broker", run_id=run.id, agent_slug=run.agent_slug
            )
            return (
                *_refusal(
                    403,
                    "network_policy",
                    f"the organisation's network policy refuses `{host}`, the host of `{connector.name}`",
                ),
                backend,
            )
    cap = int((connector.config or {}).get("requests_per_minute") or 30)
    if await _calls_this_minute(session, project.org_id, backend.provider) >= cap:
        return (
            *_refusal(
                429,
                "rate_limited",
                f"the organisation already made {cap} searches through `{connector.name}` in the last "
                "minute: retry shortly",
                retry_after_s=60,
            ),
            backend,
        )
    from choregos_adapters.web_search import SearchRateLimited

    try:
        config = await resolved_configuration(
            session, connector.org_id, connector.kind, connector.type, connector.config, connector.secret_refs
        )
        client = build(connector.kind, connector.type, config)
        async with egress_scope_for_org(session, connector.org_id, "api", f"web_search:{connector.name}"):
            found = await client.search(
                query,
                max_results=int(arguments.get("max_results") or 5),
                site=arguments.get("site"),
                recency=arguments.get("recency"),
            )
    except SearchRateLimited as limited:
        return (
            *_refusal(429, "rate_limited", redact(str(limited)), retry_after_s=limited.retry_after_s),
            backend,
        )
    except AdapterError as failed:
        return (*_refusal(502, "upstream", redact(str(failed))), backend)
    except SecretIntrouvable as missing:
        return (*_refusal(502, "upstream", redact(f"{connector.name}: {missing}")), backend)
    network = await run_network(session, run)
    reach = [*network.allow, *network.implied]
    results = []
    for hit in found[:MAX_RESULTS]:
        parts = urlsplit(str(hit.get("url", "")))
        fetchable = bool(network.allow) and parts.scheme in {"https", "http"}
        if fetchable:
            fetchable, _ = allowed(parts.hostname or "", reach, network.deny)
        results.append({**hit, "fetchable": fetchable})
    found_here = {"backend": connector.type, "results": results}
    # An MCP result: the agent reads the text block, which the injection guard reads too.
    text = json.dumps(found_here, ensure_ascii=False)
    return 200, {"content": [{"type": "text", "text": text}], "structuredContent": found_here}, backend


__all__ = [
    "INPUT_SCHEMA",
    "TOOL_NAME",
    "announcement",
    "backend_for",
    "fingerprint",
    "offered",
    "price_usd",
    "search",
]

# SPDX-License-Identifier: Apache-2.0
"""What the API plugs into the adapter registry, because it alone owns it.

The `lexical` memory keeps its facts in the API's tables (`memory_facts`, `projects`). The adapters
do not know the API: it hands them its session and its models, here, at startup, and the
orchestrator, which imports the API, does the same.

A connector at an outpost (`mcp-outpost`, ADR 0149) is the same shape: the adapters hold the tool
client and the port of the rendezvous it calls through, and the rendezvous itself is a table of the
API's, behind the outposts' door. The API registers the factory that builds the client over it; the
workers, which play governed writes, do the same.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from choregos_adapters import register
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.memory.lexical import LexicalMemory, MemoryModels

from .db.models import MemoryFact, MemoryStore, Organization, Project
from .db.session import ALL_ORGS, session_scope


def _lexical_memory(cfg: dict[str, Any]) -> LexicalMemory:
    """The memory of ONE project, in the API's tables, seen from ITS organisation.

    The factory used to receive a bare `get_sessionmaker()`: on PostgreSQL, RLS (fail-closed since
    2026-09-24) shows nothing to a session that does not name its organisation, so the adapter
    answered "unknown project" to everyone and only the SQLite tests passed. The caller says who it
    is: `org` (an API request, the resolved project) or `org: "*"` (a platform process, the
    orchestrator). With neither, the factory refuses.
    """
    org = str(cfg.get("org") or "")
    if not org:
        raise ConfigurationError(
            "lexical memory: the project's organisation is missing (`org`); without it, RLS "
            "would show nothing"
        )
    scope: str | list[str] = ALL_ORGS if org == ALL_ORGS else [org]
    return LexicalMemory(
        partial(session_scope, orgs=scope),
        MemoryModels(
            Project=Project, MemoryFact=MemoryFact, Organization=Organization, MemoryStore=MemoryStore
        ),
    )


def _outpost_tools(cfg: dict[str, Any]) -> Any:
    """The tool client of a connector at an outpost, over the API's rendezvous (ADR 0149 §3).

    What reaches this factory was built by the one seam of connector calls
    (`services/mcp_gateway.py`, `connector_client`), which injects what the platform alone has: the
    caller's session, the outpost and who the call is made for. A configuration that carries none of
    it builds nothing. Imported here, not at the top: the route's module imports the adapters'
    registry, which this module fills."""
    from .services.outpost_route import tool_client

    return tool_client(cfg)


def attach_outposts() -> None:
    """Registers the platform's factory for `mcp-outpost`, in place of the adapters' own, which can
    build nothing without a rendezvous. The declaration (the type's schema and checks) stays the
    adapters'."""
    register("mcp", "mcp-outpost")(_outpost_tools)


def attach_lexical_memory() -> None:
    """Registers the memory fallback, under its name and under its former name.

    `pgvector` is a **deprecated alias**: projects wrote it in their connector, and a data
    migration for a cosmetic rename would cost more than it brings. The console's catalogue offers
    only one of the two, and a test holds both.
    """
    register("memory", "lexical")(_lexical_memory)
    register("memory", "pgvector")(_lexical_memory)

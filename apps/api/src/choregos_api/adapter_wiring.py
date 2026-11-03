# SPDX-License-Identifier: Apache-2.0
"""What the API plugs into the adapter registry, because it alone owns it.

The `lexical` memory keeps its facts in the API's tables (`memory_facts`, `projects`). The adapters
do not know the API: it hands them its session and its models, here, at startup, and the
orchestrator, which imports the API, does the same.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from choregos_adapters import register
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.memory.lexical import LexicalMemory, MemoryModels
from choregos_core.compat import alias_names

from .db.models import MemoryFact, MemoryStore, Organization, Project
from .db.session import TOUT, session_scope


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
    scope: str | list[str] = TOUT if org == TOUT else [org]
    return LexicalMemory(
        partial(session_scope, orgs=scope),
        MemoryModels(
            Project=Project, MemoryFact=MemoryFact, Organization=Organization, MemoryStore=MemoryStore
        ),
    )


def attach_lexical_memory() -> None:
    """Registers the memory fallback, under its name and under its former name.

    `pgvector` is a **deprecated alias**: projects wrote it in their connector, and a data
    migration for a cosmetic rename would cost more than it brings. The console's catalogue offers
    only one of the two, and a test holds both.
    """
    register("memory", "lexical")(_lexical_memory)
    register("memory", "pgvector")(_lexical_memory)


alias_names(__name__, {"brancher_memoire_lexicale": "attach_lexical_memory"})  # lang: fr-ok alias:internal

"""Ce que l'API branche dans le registre des adaptateurs — parce qu'elle seule le possède.

`pgvector` range la mémoire dans les tables de l'API (`memory_facts`, `projects`). Les
adaptateurs ne connaissent pas l'API : c'est elle qui leur donne sa session et ses
modèles, ici, au démarrage — et l'orchestrateur, qui importe l'API, fait de même.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from choregos_adapters import register
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.memory.pgvector import ModelesPgVector, PgVectorMemory

from .db.models import MemoryFact, Project
from .db.session import TOUT, session_scope


def _pgvector(cfg: dict[str, Any]) -> PgVectorMemory:
    """La mémoire d'UN projet, dans les tables de l'API, vue depuis SON organisation.

    La fabrique recevait `get_sessionmaker()` nu : sur PostgreSQL, la RLS (fail-closed
    depuis le 2026-09-24) ne montre rien à une session qui ne nomme pas son organisation —
    l'adaptateur répondait « projet inconnu » à tout le monde, et seuls les tests SQLite
    passaient. L'appelant dit qui il est : `org` (une requête d'API, le projet résolu) ou
    `org: "*"` (un processus de la plateforme, l'orchestrateur). Sans rien, on refuse.
    """
    org = str(cfg.get("org") or "")
    if not org:
        raise ConfigurationError(
            "pgvector : l'organisation du projet manque (`org`) — sans elle, la RLS ne montrerait rien"
        )
    portee: str | list[str] = TOUT if org == TOUT else [org]
    return PgVectorMemory(
        partial(session_scope, orgs=portee), ModelesPgVector(Project=Project, MemoryFact=MemoryFact)
    )


def brancher_pgvector() -> None:
    register("memory", "pgvector")(_pgvector)

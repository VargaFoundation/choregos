"""Ce que l'API branche dans le registre des adaptateurs — parce qu'elle seule le possède.

La mémoire `lexical` range ses faits dans les tables de l'API (`memory_facts`, `projects`). Les
adaptateurs ne connaissent pas l'API : c'est elle qui leur donne sa session et ses
modèles, ici, au démarrage — et l'orchestrateur, qui importe l'API, fait de même.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from choregos_adapters import register
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.memory.lexicale import LexicalMemory, ModelesMemoire

from .db.models import MemoryFact, Project
from .db.session import TOUT, session_scope


def _memoire_lexicale(cfg: dict[str, Any]) -> LexicalMemory:
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
            "mémoire lexicale : l'organisation du projet manque (`org`) — sans elle, la RLS "
            "ne montrerait rien"
        )
    portee: str | list[str] = TOUT if org == TOUT else [org]
    return LexicalMemory(
        partial(session_scope, orgs=portee), ModelesMemoire(Project=Project, MemoryFact=MemoryFact)
    )


def brancher_memoire_lexicale() -> None:
    """Enregistre le repli mémoire, sous son nom et sous son ancien nom.

    `pgvector` est un **alias déprécié** : des projets l'ont écrit dans leur connecteur, et une
    migration de données pour un renommage cosmétique coûterait plus qu'elle ne rapporte. Le
    catalogue de l'interface n'en propose plus qu'un seul, et un test tient les deux.
    """
    register("memory", "lexical")(_memoire_lexicale)
    register("memory", "pgvector")(_memoire_lexicale)

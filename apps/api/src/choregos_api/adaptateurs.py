"""Ce que l'API branche dans le registre des adaptateurs — parce qu'elle seule le possède.

`pgvector` range la mémoire dans les tables de l'API (`memory_facts`, `projects`). Les
adaptateurs ne connaissent pas l'API : c'est elle qui leur donne sa session et ses
modèles, ici, au démarrage — et l'orchestrateur, qui importe l'API, fait de même.
"""

from __future__ import annotations

from choregos_adapters import register
from choregos_adapters.memory.pgvector import ModelesPgVector, PgVectorMemory

from .db.models import MemoryFact, Project
from .db.session import get_sessionmaker


def brancher_pgvector() -> None:
    register("memory", "pgvector")(
        lambda cfg: PgVectorMemory(
            get_sessionmaker(), ModelesPgVector(Project=Project, MemoryFact=MemoryFact)
        )
    )

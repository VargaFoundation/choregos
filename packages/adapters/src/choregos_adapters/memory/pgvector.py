"""MemoryAdapter de repli : pgvector dans la base de Choregos (docs/plan/04).

Moins malin qu'Ecphoria, zéro service de plus, **même interface** : c'est ce qui rend la
dépendance à Ecphoria optionnelle, comme le veut la décision « preuve avant dépendance ».
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from choregos_contracts import ContextPack, MemoryItem
from choregos_core.domain import Fact, Memory, Provenance, utcnow
from choregos_core.models import estimate_tokens
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..errors import ConfigurationError

TOKEN_RE = re.compile(r"[a-zà-ÿ0-9_]+")


def lexical_vector(text: str) -> dict[str, float]:
    """Vecteur lexical normalisé : sans service d'embeddings, la recherche reste utile."""
    tokens = [token for token in TOKEN_RE.findall(text.lower()) if len(token) > 2]
    if not tokens:
        return {}
    counts: dict[str, float] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in counts.values()))
    return {token: value / norm for token, value in counts.items()}


def similarity(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    return sum(value * large.get(token, 0.0) for token, value in small.items())


@dataclass(frozen=True)
class ModelesPgVector:
    """Les deux tables dont cet adaptateur a besoin, INJECTÉES par l'application qui les possède.

    L'adaptateur importait `choregos_api.db.*` — une dépendance inversée (les adaptateurs
    ne connaissent pas l'API) et non déclarée, cachée derrière des imports paresseux : le
    wheel de `choregos-adapters` explosait en `ModuleNotFoundError` dès qu'on touchait
    pgvector hors de l'espace de travail (état des lieux du 2026-09-24).
    """

    Project: Any
    MemoryFact: Any


class PgVectorMemory:
    """Mémoire stockée dans `memory_facts`, avec supersession par sujet."""

    def __init__(
        self, sessionmaker: async_sessionmaker[Any] | None = None, modeles: ModelesPgVector | None = None
    ) -> None:
        self._sessionmaker = sessionmaker
        self._modeles = modeles

    def _sessions(self) -> async_sessionmaker[Any]:
        if self._sessionmaker is None:
            raise ConfigurationError(
                "pgvector : aucune session injectée — l'application doit enregistrer la fabrique "
                "(`choregos_api.adaptateurs.brancher_pgvector`)"
            )
        return self._sessionmaker

    @property
    def modeles(self) -> ModelesPgVector:
        if self._modeles is None:
            raise ConfigurationError("pgvector : modèles non injectés (voir `brancher_pgvector`)")
        return self._modeles

    async def _project_id(self, session: Any, project: str) -> str:
        Project = self.modeles.Project  # noqa: N806 — c'est une classe

        row = (await session.execute(select(Project).where(Project.slug == project))).scalar_one_or_none()
        if row is None:
            row = await session.get(Project, project)
        if row is None:
            raise ValueError(f"projet inconnu pour la mémoire : {project}")
        return str(row.id)

    async def context_pack(
        self, project: str, query: str, paths: list[str], budget_tokens: int, kinds: list[str] | None = None
    ) -> ContextPack:
        if budget_tokens <= 0:
            return ContextPack.empty(query)
        rows = await self._ranked(project, query, kinds, limit=40)
        memories: list[MemoryItem] = []
        incidents: list[MemoryItem] = []
        tokens = 0
        truncated = False
        for row, raw_score in rows:
            # Un fait qui ne concerne pas les chemins du ticket compte un peu moins.
            off_path = bool(paths and row.paths and not any(path in row.paths for path in paths))
            score = raw_score * (0.8 if off_path else 1.0)
            item = MemoryItem(
                id=row.id,
                kind=row.kind,
                subject=row.subject,
                content=row.content,
                score=round(score, 4),
                valid_from=row.valid_from.isoformat() if row.valid_from else None,
                provenance=row.provenance or {},
            )
            cost = estimate_tokens(item.content)
            if tokens + cost > budget_tokens:
                truncated = True
                break
            tokens += cost
            (incidents if row.kind == "incident" else memories).append(item)
        return ContextPack(
            query=query,
            paths=list(paths),
            kinds=list(kinds or []),
            budget_tokens=budget_tokens,
            tokens_estimated=tokens,
            truncated=truncated,
            memories=memories,
            incidents=incidents,
            generated_at=utcnow().isoformat(),
        )

    async def _ranked(
        self, project: str, query: str, kinds: list[str] | None, limit: int
    ) -> list[tuple[Any, float]]:
        MemoryFact = self.modeles.MemoryFact  # noqa: N806

        vector = lexical_vector(query)
        async with self._sessions()() as session:
            project_id = await self._project_id(session, project)
            statement = select(MemoryFact).where(
                MemoryFact.project_id == project_id, MemoryFact.status == "active"
            )
            if kinds:
                statement = statement.where(MemoryFact.kind.in_(kinds))
            rows = (await session.execute(statement)).scalars().all()
        scored = [(row, similarity(vector, lexical_vector(f"{row.subject} {row.content}"))) for row in rows]
        scored.sort(key=lambda pair: (-pair[1], pair[0].subject))
        return [pair for pair in scored[:limit] if pair[1] > 0 or not query]

    async def write_fact(self, project: str, fact: Fact) -> str:
        """Écriture gouvernée : le fait précédent de même `subject` est superseded."""
        MemoryFact = self.modeles.MemoryFact  # noqa: N806

        async with self._sessions()() as session:
            project_id = await self._project_id(session, project)
            previous = (
                (
                    await session.execute(
                        select(MemoryFact).where(
                            MemoryFact.project_id == project_id,
                            MemoryFact.subject == fact.subject,
                            MemoryFact.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in previous:
                if fact.external_id and row.external_id == fact.external_id and row.content == fact.content:
                    return str(row.id)  # upsert idempotent
                row.status = "superseded"
                row.valid_to = utcnow()
            created = MemoryFact(
                project_id=project_id,
                kind=str(fact.kind),
                subject=fact.subject,
                content=fact.content,
                external_id=fact.external_id,
                source=(fact.provenance.source if fact.provenance else "orchestrator"),
                status="active",
                valid_from=fact.valid_from or utcnow(),
                provenance=fact.provenance.model_dump(mode="json") if fact.provenance else {},
                paths=list(fact.paths),
            )
            session.add(created)
            await session.flush()
            memory_id = str(created.id)
            await session.commit()
            return memory_id

    async def propose_fact(self, project: str, fact: Fact, provenance: Provenance) -> str:
        MemoryFact = self.modeles.MemoryFact  # noqa: N806

        async with self._sessions()() as session:
            project_id = await self._project_id(session, project)
            created = MemoryFact(
                project_id=project_id,
                kind=str(fact.kind),
                subject=fact.subject,
                content=fact.content,
                external_id=fact.external_id,
                source=provenance.source,
                status="pending",
                provenance=provenance.model_dump(mode="json"),
                proposed_by=provenance.run_id,
                paths=list(fact.paths),
            )
            session.add(created)
            await session.flush()
            memory_id = str(created.id)
            await session.commit()
            return memory_id

    async def list_pending(self, project: str) -> list[Memory]:
        MemoryFact = self.modeles.MemoryFact  # noqa: N806

        async with self._sessions()() as session:
            project_id = await self._project_id(session, project)
            rows = (
                (
                    await session.execute(
                        select(MemoryFact).where(
                            MemoryFact.project_id == project_id, MemoryFact.status == "pending"
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [_memory(row) for row in rows]

    async def accept_pending(self, project: str, memory_id: str) -> bool:
        return await self._set_status(memory_id, "active")

    async def reject_pending(self, project: str, memory_id: str) -> bool:
        return await self._set_status(memory_id, "rejected")

    async def _set_status(self, memory_id: str, status: str) -> bool:
        MemoryFact = self.modeles.MemoryFact  # noqa: N806

        async with self._sessions()() as session:
            row = await session.get(MemoryFact, memory_id)
            if row is None:
                return False
            row.status = status
            if status == "active":
                row.valid_from = row.valid_from or utcnow()
            await session.commit()
            return True

    async def ingest_events(self, project: str, events: list[dict[str, Any]]) -> None:
        for event in events:
            await self.write_fact(
                project,
                Fact(
                    kind=event.get("kind", "other"),
                    subject=str(event.get("subject", "")),
                    content=str(event.get("content", "")),
                    external_id=event.get("external_id"),
                    provenance=Provenance(
                        source=str(event.get("source", "ingest")), ref=event.get("external_id")
                    ),
                ),
            )

    async def search(self, project: str, query: str, k: int) -> list[Memory]:
        rows = await self._ranked(project, query, None, limit=k)
        return [_memory(row, score) for row, score in rows]

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "backend": "pgvector (repli)"}


def _memory(row: Any, score: float | None = None) -> Memory:
    return Memory(
        id=str(row.id),
        kind=row.kind,
        subject=row.subject,
        content=row.content,
        score=score,
        status=row.status,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        provenance=row.provenance or {},
        proposed_by=row.proposed_by,
    )

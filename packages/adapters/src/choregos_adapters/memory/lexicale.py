# SPDX-License-Identifier: Apache-2.0
"""MemoryAdapter de repli : recherche **lexicale**, dans la base de Choregos.

Ce connecteur s'est appelé `pgvector` jusqu'au 2026-09-26, et le nom mentait sur trois
points : il n'y a aucune extension `vector` dans les migrations, la colonne `embedding` est
un `Json`, et la similarité est un produit scalaire entre sacs de mots calculé en Python.
Personne n'avait menti — le nom disait l'intention du jour où il a été écrit — mais un
exploitant qui lit « pgvector » en conclut qu'il a de la recherche sémantique, et il n'en a
pas. Le type de connecteur s'appelle donc `lexical`, et `pgvector` reste accepté en alias
déprécié pour les projets déjà configurés.

Moins malin qu'Ecphoria, zéro service de plus, **même interface** : c'est ce qui rend la
dépendance à Ecphoria optionnelle, comme le veut la décision « preuve avant dépendance ».
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from choregos_contracts import ContextPack, MemoryItem
from choregos_core.domain import Fact, Memory, Provenance, utcnow
from choregos_core.memory_scope import parse_namespace
from choregos_core.models import estimate_tokens

from ..errors import ConfigurationError

#: Une fabrique de sessions : appelée sans argument, elle rend un `async with` qui donne
#: une session. Un `async_sessionmaker` convient ; une `session_scope(orgs=…)` partielle
#: aussi — et c'est celle-là qu'il faut sur PostgreSQL, parce que la RLS ne montre rien à
#: une session qui ne nomme pas son organisation.
Sessions = Callable[[], AbstractAsyncContextManager[Any]]

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
class ModelesMemoire:
    """Les deux tables dont cet adaptateur a besoin, INJECTÉES par l'application qui les possède.

    L'adaptateur importait `choregos_api.db.*` — une dépendance inversée (les adaptateurs
    ne connaissent pas l'API) et non déclarée, cachée derrière des imports paresseux : le
    wheel de `choregos-adapters` explosait en `ModuleNotFoundError` dès qu'on touchait
    le repli mémoire hors de l'espace de travail (état des lieux du 2026-09-24).
    """

    Project: Any
    MemoryFact: Any
    #: The two tables named memories need (S40-11); optional so a caller that only reads the
    #: project's shared and own memories keeps working without them.
    Organization: Any = None
    MemoryStore: Any = None


@dataclass(frozen=True)
class Home:
    """Where the facts of a namespace live (S40-11)."""

    org_id: str
    project_id: str | None
    store_id: str | None
    agent_slug: str | None


class LexicalMemory:
    """Mémoire stockée dans `memory_facts`, avec supersession par sujet."""

    def __init__(self, sessions: Sessions | None = None, modeles: ModelesMemoire | None = None) -> None:
        """`sessions` porte déjà la portée RLS (l'organisation du projet, ou `*` pour un
        processus de la plateforme) : l'adaptateur ne la connaît pas et ne la pose pas.
        Avec un `async_sessionmaker` nu, il tournait sur PostgreSQL sans jamais rien voir —
        « projet inconnu » sur tout projet, seul SQLite le faisait passer."""
        self._sessionmaker = sessions
        self._modeles = modeles

    def _sessions(self) -> Sessions:
        if self._sessionmaker is None:
            raise ConfigurationError(
                "mémoire lexicale : aucune session injectée — l'application doit enregistrer la "
                "fabrique (`choregos_api.adaptateurs.brancher_memoire_lexicale`)"
            )
        return self._sessionmaker

    @property
    def modeles(self) -> ModelesMemoire:
        if self._modeles is None:
            raise ConfigurationError("pgvector : modèles non injectés (voir `brancher_pgvector`)")
        return self._modeles

    @staticmethod
    def _scope(memory_fact: Any, home: Home) -> Any:
        """The facts of a namespace: a named memory's (S40-11), the project's own (no agent, no
        store), or one agent's within it (S32-02)."""
        from sqlalchemy import and_

        if home.store_id is not None:
            return memory_fact.memory_store_id == home.store_id
        portee = (
            memory_fact.agent_slug.is_(None)
            if home.agent_slug is None
            else memory_fact.agent_slug == home.agent_slug
        )
        return and_(memory_fact.project_id == home.project_id, memory_fact.memory_store_id.is_(None), portee)

    async def _home(self, session: Any, namespace: str) -> Home:
        parsed = parse_namespace(namespace)
        modeles = self.modeles
        if parsed.kind == "global_store":
            if modeles.Organization is None or modeles.MemoryStore is None:
                raise ConfigurationError("lexical memory: the organisation and store models are not injected")
            organisation = (
                await session.execute(
                    _select(modeles.Organization).where(modeles.Organization.slug == parsed.org)
                )
            ).scalar_one_or_none()
            if organisation is None:
                raise ValueError(f"unknown organisation for the memory: {parsed.org}")
            store = (
                await session.execute(
                    _select(modeles.MemoryStore).where(
                        modeles.MemoryStore.org_id == organisation.id,
                        modeles.MemoryStore.owner_key == "org",
                        modeles.MemoryStore.slug == parsed.store,
                    )
                )
            ).scalar_one_or_none()
            if store is None:
                raise ValueError(f"unknown global memory: {parsed.org}/{parsed.store}")
            return Home(str(organisation.id), None, str(store.id), None)
        Project = modeles.Project  # noqa: N806 — a class
        row = (
            await session.execute(_select(Project).where(Project.slug == parsed.project))
        ).scalar_one_or_none()
        if row is None:
            row = await session.get(Project, parsed.project)
        if row is None:
            unknown = f"projet inconnu pour la mémoire : {parsed.project}"  # lang: fr-ok (pinned by a test)
            raise ValueError(unknown)
        store_id = None
        if parsed.kind == "project_store":
            if modeles.MemoryStore is None:
                raise ConfigurationError("lexical memory: the store model is not injected")
            store = (
                await session.execute(
                    _select(modeles.MemoryStore).where(
                        modeles.MemoryStore.org_id == row.org_id,
                        modeles.MemoryStore.owner_key == str(row.id),
                        modeles.MemoryStore.slug == parsed.store,
                    )
                )
            ).scalar_one_or_none()
            if store is None:
                raise ValueError(f"unknown project memory: {parsed.project}/{parsed.store}")
            store_id = str(store.id)
        return Home(str(row.org_id), str(row.id), store_id, parsed.agent)

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
            home = await self._home(session, project)
            statement = _select(MemoryFact).where(
                self._scope(MemoryFact, home), MemoryFact.status == "active"
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
            home = await self._home(session, project)
            previous = (
                (
                    await session.execute(
                        _select(MemoryFact).where(
                            self._scope(MemoryFact, home),
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
                org_id=home.org_id,
                project_id=home.project_id,
                memory_store_id=home.store_id,
                agent_slug=home.agent_slug,
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
            home = await self._home(session, project)
            if fact.external_id:
                # Idempotent: proposing the same thing again (a replayed activity) is one proposal.
                same = (
                    await session.execute(
                        _select(MemoryFact.id).where(
                            self._scope(MemoryFact, home),
                            MemoryFact.external_id == fact.external_id,
                            MemoryFact.status.in_(["pending", "active"]),
                        )
                    )
                ).first()
                if same is not None:
                    return str(same[0])
            created = MemoryFact(
                org_id=home.org_id,
                project_id=home.project_id,
                memory_store_id=home.store_id,
                agent_slug=home.agent_slug,
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
            home = await self._home(session, project)
            rows = (
                (
                    await session.execute(
                        _select(MemoryFact).where(
                            self._scope(MemoryFact, home), MemoryFact.status == "pending"
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [_memory(row) for row in rows]

    async def accept_pending(self, project: str, memory_id: str) -> bool:
        return await self._set_status(project, memory_id, "active")

    async def reject_pending(self, project: str, memory_id: str) -> bool:
        return await self._set_status(project, memory_id, "rejected")

    async def _set_status(self, namespace: str, memory_id: str, status: str) -> bool:
        MemoryFact = self.modeles.MemoryFact  # noqa: N806

        async with self._sessions()() as session:
            home = await self._home(session, namespace)
            row = (
                await session.execute(
                    _select(MemoryFact).where(MemoryFact.id == memory_id, self._scope(MemoryFact, home))
                )
            ).scalar_one_or_none()
            # A fact outside the namespace it is decided in does not exist for this decision.
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


def _select(*entities: Any) -> Any:
    """`sqlalchemy.select`, imported on first use.

    The runner image imports `choregos_adapters` (for `sealing`) without SQLAlchemy;
    a module-level import here made `choregos-runner --help` fail in that image.
    """
    from sqlalchemy import select

    return select(*entities)


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

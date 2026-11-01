# SPDX-License-Identifier: Apache-2.0
"""The fallback MemoryAdapter: **lexical** search, in Choregos's own database.

This connector was called `pgvector` until 2026-09-26, and the name lied on three counts: there is
no `vector` extension in the migrations, the `embedding` column is a `Json`, and the similarity is
a dot product between bags of words computed in Python. Nobody lied — the name said the intention
of the day it was written — but an operator who reads "pgvector" concludes they have semantic
search, and they do not. The connector type is therefore called `lexical`, and `pgvector` is still
accepted as a deprecated alias for the projects already configured.

Less clever than Ecphoria, zero more services, **same interface**: that is what makes the
dependency on Ecphoria optional, as the decision "proof before dependency" wants.
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
from choregos_core.memory_scope import memory_key, parse_namespace
from choregos_core.models import estimate_tokens

from ..errors import ConfigurationError

#: A session factory: called without argument, it returns an `async with` that yields a
#: session. An `async_sessionmaker` will do; so will a partial `session_scope(orgs=…)` — and that is
#: the one to use on PostgreSQL, because RLS shows nothing to a session that does not name its
#: organisation.
Sessions = Callable[[], AbstractAsyncContextManager[Any]]

TOKEN_RE = re.compile(r"[a-zà-ÿ0-9_]+")


def lexical_vector(text: str) -> dict[str, float]:
    """A normalised lexical vector: without an embeddings service, search stays useful."""
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
class MemoryModels:
    """The two tables this adapter needs, INJECTED by the application that owns them.

    The adapter used to import `choregos_api.db.*` — an inverted dependency (the adapters do not
    know the API) and an undeclared one, hidden behind lazy imports: the `choregos-adapters` wheel
    blew up with `ModuleNotFoundError` as soon as the memory fallback was touched outside the
    workspace (inventory of 2026-09-24).
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
    """Memory stored in `memory_facts`, with supersession by subject."""

    def __init__(self, sessions: Sessions | None = None, models: MemoryModels | None = None) -> None:
        """`sessions` already carries the RLS scope (the project's organisation, or `*` for a
        platform process): the adapter does not know it and does not set it. With a bare
        `async_sessionmaker`, it ran on PostgreSQL without ever seeing anything — "unknown project"
        on every project, only SQLite let it through."""
        self._sessionmaker = sessions
        self._models = models

    def _sessions(self) -> Sessions:
        if self._sessionmaker is None:
            raise ConfigurationError(
                "lexical memory: no session injected — the application must register the "
                "factory (`choregos_api.adaptateurs.brancher_memoire_lexicale`)"
            )
        return self._sessionmaker

    @property
    def models(self) -> MemoryModels:
        if self._models is None:
            raise ConfigurationError("lexical memory: models not injected (see `brancher_memoire_lexicale`)")
        return self._models

    @property
    def modeles(self) -> MemoryModels:  # lang: fr-ok alias:internal
        return self.models

    @staticmethod
    def _scope(memory_fact: Any, home: Home) -> Any:
        """The facts of a namespace: a named memory's (S40-11), the project's own (no agent, no
        store), or one agent's within it (S32-02)."""
        from sqlalchemy import and_

        if home.store_id is not None:
            return memory_fact.memory_store_id == home.store_id
        scope = (
            memory_fact.agent_slug.is_(None)
            if home.agent_slug is None
            else memory_fact.agent_slug == home.agent_slug
        )
        return and_(memory_fact.project_id == home.project_id, memory_fact.memory_store_id.is_(None), scope)

    async def _home(self, session: Any, namespace: str) -> Home:
        parsed = parse_namespace(namespace)
        models = self.models
        if parsed.kind == "global_store":
            if models.Organization is None or models.MemoryStore is None:
                raise ConfigurationError("lexical memory: the organisation and store models are not injected")
            # `parsed.org` is the organisation's memory key (S40-24): the slug it had when its slug
            # first changed, else its slug.
            candidates = (await session.execute(_select(models.Organization))).scalars().all()
            organisation = next((o for o in candidates if memory_key(o) == parsed.org), None)
            if organisation is None:
                raise ValueError(f"unknown organisation for the memory: {parsed.org}")
            store = (
                await session.execute(
                    _select(models.MemoryStore).where(
                        models.MemoryStore.org_id == organisation.id,
                        models.MemoryStore.owner_key == "org",
                        models.MemoryStore.slug == parsed.store,
                    )
                )
            ).scalar_one_or_none()
            if store is None:
                raise ValueError(f"unknown global memory: {parsed.org}/{parsed.store}")
            return Home(str(organisation.id), None, str(store.id), None)
        Project = models.Project  # noqa: N806 — a class
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
            if models.MemoryStore is None:
                raise ConfigurationError("lexical memory: the store model is not injected")
            store = (
                await session.execute(
                    _select(models.MemoryStore).where(
                        models.MemoryStore.org_id == row.org_id,
                        models.MemoryStore.owner_key == str(row.id),
                        models.MemoryStore.slug == parsed.store,
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
            # A fact that does not concern the ticket's paths counts a little less.
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
        MemoryFact = self.models.MemoryFact  # noqa: N806

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
        """Governed write: the previous fact with the same `subject` is superseded."""
        MemoryFact = self.models.MemoryFact  # noqa: N806

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
        MemoryFact = self.models.MemoryFact  # noqa: N806

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
        MemoryFact = self.models.MemoryFact  # noqa: N806

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
        MemoryFact = self.models.MemoryFact  # noqa: N806

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
        return {"ok": True, "backend": "pgvector (fallback)"}


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


# Former names that other code imports (ADR 0098 §3 (b)); `memory/lexicale.py` keeps the former path.
ModelesMemoire = MemoryModels  # lang: fr-ok alias:internal

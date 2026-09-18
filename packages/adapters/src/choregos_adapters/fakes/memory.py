"""Mémoire en mémoire (sic) : recherche lexicale, supersession par sujet, file `pending`."""

from __future__ import annotations

from typing import Any

from choregos_contracts import ContextPack, MemoryItem, RelatedItem
from choregos_core.domain import Fact, Memory, Provenance, utcnow
from choregos_core.models import estimate_tokens


class FakeMemory:
    """Mémoire de test : suffisante pour tester le context pack, la supersession et la file pending."""

    def __init__(self) -> None:
        self.facts: dict[str, list[Memory]] = {}
        self.pending: dict[str, list[Memory]] = {}
        self.ingested: list[dict[str, Any]] = []
        self.fail = False  # simule une panne : le context pack doit être vide, pas bloquant
        self._next_id = 1

    def _new_id(self) -> str:
        value = f"mem-{self._next_id}"
        self._next_id += 1
        return value

    def seed(
        self, project: str, kind: str, subject: str, content: str, paths: list[str] | None = None
    ) -> str:
        memory = Memory(
            id=self._new_id(),
            kind=kind,
            subject=subject,
            content=content,
            valid_from=utcnow(),
            provenance={"source": "seed", "paths": paths or []},
        )
        self.facts.setdefault(project, []).append(memory)
        return memory.id

    async def context_pack(
        self, project: str, query: str, paths: list[str], budget_tokens: int, kinds: list[str] | None = None
    ) -> ContextPack:
        if self.fail:
            return ContextPack.empty(query)
        terms = {w.lower() for w in query.split() if len(w) > 3}
        selected: list[MemoryItem] = []
        incidents: list[MemoryItem] = []
        tokens = 0
        truncated = False
        for memory in self._ranked(project, terms, kinds):
            item = MemoryItem(
                id=memory.id,
                kind=str(memory.kind),
                subject=memory.subject,
                content=memory.content,
                score=memory.score,
                valid_from=memory.valid_from.isoformat() if memory.valid_from else None,
                provenance=memory.provenance,
            )
            cost = estimate_tokens(item.content)
            if tokens + cost > budget_tokens:
                truncated = True
                break
            tokens += cost
            (incidents if str(memory.kind) == "incident" else selected).append(item)
        return ContextPack(
            query=query,
            paths=list(paths),
            kinds=list(kinds or []),
            budget_tokens=budget_tokens,
            tokens_estimated=tokens,
            truncated=truncated,
            memories=selected,
            incidents=incidents,
            related_items=[RelatedItem(key=f"{project}#1", title="Ticket voisin")] if selected else [],
            generated_at=utcnow().isoformat(),
        )

    def _ranked(self, project: str, terms: set[str], kinds: list[str] | None) -> list[Memory]:
        out: list[Memory] = []
        for memory in self.facts.get(project, []):
            if memory.status != "active":
                continue
            if kinds and str(memory.kind) not in kinds:
                continue
            haystack = f"{memory.subject} {memory.content}".lower()
            score = sum(1 for term in terms if term in haystack) / (len(terms) or 1)
            out.append(memory.model_copy(update={"score": score}))
        return sorted(out, key=lambda m: (-(m.score or 0), m.subject))

    async def write_fact(self, project: str, fact: Fact) -> str:
        """Écriture gouvernée : un nouveau fait supersède le précédent de même `subject`."""
        for memory in self.facts.get(project, []):
            if memory.subject == fact.subject and memory.status == "active":
                memory.status = "superseded"
                memory.valid_to = utcnow()
        memory = Memory(
            id=self._new_id(),
            kind=fact.kind,
            subject=fact.subject,
            content=fact.content,
            valid_from=fact.valid_from or utcnow(),
            provenance=fact.provenance.model_dump(mode="json") if fact.provenance else {},
        )
        self.facts.setdefault(project, []).append(memory)
        return memory.id

    async def propose_fact(self, project: str, fact: Fact, provenance: Provenance) -> str:
        memory = Memory(
            id=self._new_id(),
            kind=fact.kind,
            subject=fact.subject,
            content=fact.content,
            status="pending",
            provenance=provenance.model_dump(mode="json"),
            proposed_by=provenance.run_id,
        )
        self.pending.setdefault(project, []).append(memory)
        return memory.id

    async def accept_pending(self, project: str, memory_id: str) -> bool:
        for memory in self.pending.get(project, []):
            if memory.id == memory_id:
                memory.status = "active"
                self.pending[project].remove(memory)
                self.facts.setdefault(project, []).append(memory)
                return True
        return False

    async def ingest_events(self, project: str, events: list[dict[str, Any]]) -> None:
        """Upsert idempotent par `external_id`."""
        for event in events:
            external_id = event.get("external_id")
            existing = next(
                (m for m in self.facts.get(project, []) if m.provenance.get("external_id") == external_id),
                None,
            )
            if existing and external_id:
                if existing.content != event.get("content", ""):
                    existing.status = "superseded"
                    existing.valid_to = utcnow()
                else:
                    continue
            self.ingested.append(event)
            self.facts.setdefault(project, []).append(
                Memory(
                    id=self._new_id(),
                    kind=event.get("kind", "other"),
                    subject=event.get("subject", ""),
                    content=event.get("content", ""),
                    valid_from=utcnow(),
                    provenance={"external_id": external_id, "source": event.get("source", "ingest")},
                )
            )

    async def search(self, project: str, query: str, k: int) -> list[Memory]:
        terms = {w.lower() for w in query.split() if len(w) > 2}
        return self._ranked(project, terms, None)[:k]

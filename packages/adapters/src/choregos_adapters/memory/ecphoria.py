"""MemoryAdapter Ecphoria : mémoire bi-temporelle et base de connaissance (docs/plan/04).

Deux règles tiennent tout : lecture à **timeout court** (une mémoire lente ne bloque jamais
un stage — on rend un pack vide), et écriture **gouvernée** (l'orchestrateur écrit, l'agent
propose).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from choregos_contracts import ContextPack, MemoryItem, RelatedItem
from choregos_core.domain import Fact, Memory, Provenance, utcnow

from ..errors import UpstreamError


class EcphoriaMemory:
    """Client REST d'Ecphoria, un tenant par projet."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        tenant: str | None = None,
        read_timeout_ms: int = 300,
        write_timeout_s: float = 2.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.tenant = tenant
        self.read_timeout = read_timeout_ms / 1000
        self.write_timeout = write_timeout_s
        self._client = client or httpx.AsyncClient(timeout=write_timeout_s)
        self._breaker_until = 0.0
        self._failures = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self, project: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "X-Ecphoria-Tenant": self.tenant or project}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _circuit_open(self) -> bool:
        import time

        return time.monotonic() < self._breaker_until

    def _trip(self) -> None:
        """Circuit-breaker : après 3 échecs, on cesse d'attendre pendant 30 secondes."""
        import time

        self._failures += 1
        if self._failures >= 3:
            self._breaker_until = time.monotonic() + 30
            self._failures = 0

    async def _request(self, method: str, path: str, *, project: str, timeout: float, **kwargs: Any) -> Any:
        response = await self._client.request(
            method, f"{self.base_url}{path}", headers=self._headers(project), timeout=timeout, **kwargs
        )
        if response.status_code >= 400:
            raise UpstreamError(
                "ecphoria",
                f"{method} {path} → {response.status_code} : {response.text[:200]}",
                status_code=response.status_code,
            )
        return response.json() if response.content else {}

    async def context_pack(
        self, project: str, query: str, paths: list[str], budget_tokens: int, kinds: list[str] | None = None
    ) -> ContextPack:
        """Endpoint composite E-05. Toute panne rend un pack **vide**, jamais une erreur."""
        if budget_tokens <= 0 or self._circuit_open():
            return ContextPack.empty(query)
        try:
            payload = await asyncio.wait_for(
                self._request(
                    "POST",
                    "/api/v1/context-pack",
                    project=project,
                    timeout=self.read_timeout,
                    json={
                        "query": query,
                        "paths": paths,
                        "kinds": kinds or [],
                        "budget_tokens": budget_tokens,
                        "k": 20,
                    },
                ),
                timeout=self.read_timeout * 2,
            )
        except (TimeoutError, UpstreamError, httpx.HTTPError):
            self._trip()
            return ContextPack.empty(query)
        self._failures = 0
        return ContextPack(
            query=query,
            paths=list(paths),
            kinds=list(kinds or []),
            budget_tokens=budget_tokens,
            tokens_estimated=int(payload.get("tokens_estimated", 0)),
            truncated=bool(payload.get("truncated", False)),
            memories=[_memory_item(m) for m in payload.get("memories", [])],
            incidents=[_memory_item(m) for m in payload.get("incidents", [])],
            related_items=[
                RelatedItem(
                    key=str(item.get("key", "")),
                    title=str(item.get("title", "")),
                    state=item.get("state"),
                    url=item.get("url"),
                    summary=item.get("summary"),
                )
                for item in payload.get("related_items", [])
            ],
            generated_at=utcnow().isoformat(),
        )

    async def write_fact(self, project: str, fact: Fact) -> str:
        """Upsert idempotent par `external_id` (E-06) ; la supersession est faite par Ecphoria."""
        body = _fact_body(fact)
        if fact.external_id:
            payload = await self._request(
                "PUT",
                "/api/v1/memories/by-external-id",
                project=project,
                timeout=self.write_timeout,
                json={
                    **body,
                    "external_id": fact.external_id,
                    "source": (fact.provenance.source if fact.provenance else "orchestrator"),
                },
            )
        else:
            payload = await self._request(
                "POST", "/api/v1/memories", project=project, timeout=self.write_timeout, json=body
            )
        return _written_id(payload)

    async def propose_fact(self, project: str, fact: Fact, provenance: Provenance) -> str:
        payload = await self._request(
            "POST",
            "/api/v1/memories",
            project=project,
            timeout=self.write_timeout,
            params={"status": "pending"},
            json=_with_provenance(_fact_body(fact), provenance),
        )
        return _written_id(payload)

    async def list_pending(self, project: str) -> list[Memory]:
        payload = await self._request("GET", "/api/v1/pending", project=project, timeout=self.write_timeout)
        return [
            _memory(entry) for entry in payload.get("items", payload if isinstance(payload, list) else [])
        ]

    async def accept_pending(self, project: str, memory_id: str) -> bool:
        await self._request(
            "POST", f"/api/v1/pending/{memory_id}/accept", project=project, timeout=self.write_timeout
        )
        return True

    async def reject_pending(self, project: str, memory_id: str) -> bool:
        await self._request(
            "POST", f"/api/v1/pending/{memory_id}/reject", project=project, timeout=self.write_timeout
        )
        return True

    async def ingest_events(self, project: str, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        await self._request(
            "POST",
            "/api/v1/memories/batch",
            project=project,
            timeout=max(self.write_timeout, 10.0),
            json={"memories": [_ingest_body(event) for event in events]},
        )

    async def search(self, project: str, query: str, k: int) -> list[Memory]:
        payload = await self._request(
            "POST",
            "/api/v1/memories/search",
            project=project,
            timeout=max(self.read_timeout * 3, 1.0),
            json={"query": query, "k": k},
        )
        # Un hit porte la mémoire et son score : `{"memory": {…}, "score": 0.42}`.
        return [
            _memory({**hit.get("memory", hit), "score": hit.get("score")})
            for hit in payload.get("results", [])
        ]

    async def create_tenant(self, project: str) -> None:
        await self._request(
            "POST",
            "/api/v1/admin/tenants",
            project=project,
            timeout=self.write_timeout,
            json={"name": project, "require_provenance": True},
        )

    async def test(self) -> dict[str, Any]:
        try:
            payload = await self._request("GET", "/health", project=self.tenant or "", timeout=2.0)
        except (UpstreamError, httpx.HTTPError) as exc:
            return {"ok": False, "error": str(exc)[:200]}
        return {"ok": True, "status": payload}


def _written_id(payload: dict[str, Any]) -> str:
    """L'identifiant d'une écriture.

    Ecphoria répond `{memory, outcome}` sur un ajout et `{id, …}` sur un upsert ou une
    proposition : les deux formes désignent la même chose, on ne veut pas que l'appelant
    ait à le savoir.
    """
    if payload.get("id"):
        return str(payload["id"])
    return str((payload.get("memory") or {}).get("id", ""))


def _fact_body(fact: Fact) -> dict[str, Any]:
    """Corps d'écriture Ecphoria.

    `kind`, `paths` et `provenance` sont des notions Choregos : Ecphoria les porte dans
    `metadata`, et c'est là que le context pack va les relire pour filtrer par type et par
    chemin autorisé. Les envoyer à la racine revenait à les perdre en silence.
    """
    body: dict[str, Any] = {
        "subject": fact.subject,
        "content": fact.content,
        "metadata": {
            "kind": str(fact.kind),
            "paths": list(fact.paths or []),
            "provenance": fact.provenance.model_dump(mode="json") if fact.provenance else {},
        },
    }
    if fact.valid_from:
        body["valid_from"] = fact.valid_from.isoformat()
    return body


def _ingest_body(event: dict[str, Any]) -> dict[str, Any]:
    source = event.get("source", "choregos")
    return {
        "subject": event.get("subject", ""),
        "content": event.get("content", ""),
        "metadata": {
            "kind": event.get("kind", "other"),
            "external_id": event.get("external_id"),
            "source": source,
            "provenance": {"source": source, "ref": event.get("external_id")},
        },
    }


def _with_provenance(body: dict[str, Any], provenance: Provenance) -> dict[str, Any]:
    metadata = {**body.get("metadata", {}), "provenance": provenance.model_dump(mode="json")}
    return {**body, "metadata": metadata}


def _memory_item(payload: dict[str, Any]) -> MemoryItem:
    return MemoryItem(
        id=payload.get("id"),
        kind=str(payload.get("kind", "other")),
        subject=str(payload.get("subject", "")),
        content=str(payload.get("content", "")),
        score=payload.get("score"),
        valid_from=payload.get("valid_from"),
        valid_to=payload.get("valid_to"),
        provenance=payload.get("provenance") or {},
    )


def _memory(payload: dict[str, Any]) -> Memory:
    """Mémoire Ecphoria → modèle Choregos.

    Ecphoria range le type, les chemins et la provenance dans `metadata`, et nomme son cycle
    de vie `state` (`active` / `pending` / `superseded` / `expired`). On traduit les deux.
    """
    metadata = payload.get("metadata") or {}
    provenance = metadata.get("provenance") or payload.get("provenance") or {}
    state = str(payload.get("state") or payload.get("status") or "active")
    return Memory(
        id=str(payload.get("id", "")),
        kind=metadata.get("kind") or payload.get("kind") or "other",
        subject=str(payload.get("subject") or ""),
        content=str(payload.get("content", "")),
        score=payload.get("score"),
        status="rejected" if state == "expired" else state,
        valid_from=payload.get("valid_from"),
        valid_to=payload.get("valid_to"),
        provenance=provenance,
        proposed_by=provenance.get("run_id"),
    )

# SPDX-License-Identifier: Apache-2.0
"""The internal API client: the runner's only way out."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from choregos_contracts import ContextPack, Finding, StageInput, StageResult


class InternalApiError(RuntimeError):
    """The internal API refused a call."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"internal API: {status_code} — {detail}")
        self.status_code = status_code
        self.detail = detail


class InternalClient:
    """Calls to the API's `/internal`, authenticated with the run token."""

    def __init__(self, base_url: str, run_id: str, token: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.run_id = run_id
        self._client = httpx.AsyncClient(timeout=timeout, headers={"Authorization": f"Bearer {token}"})
        self._event_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> InternalClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _url(self, suffix: str) -> str:
        return f"{self.base_url}/runs/{self.run_id}{suffix}"

    async def _request(self, method: str, suffix: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, self._url(suffix), **kwargs)
        if response.status_code >= 400:
            raise InternalApiError(response.status_code, response.text[:400])
        return response.json() if response.content else None

    async def fetch_input(self) -> StageInput:
        return StageInput.model_validate(await self._request("GET", "/input"))

    async def post_events(self, events: list[dict[str, Any]]) -> int:
        """The ACP log in batches; the API deduplicates by `seq`, so a replay has no effect."""
        if not events:
            return 0
        async with self._event_lock:
            payload = await self._request("POST", "/events", json={"events": events})
        return int((payload or {}).get("accepted", 0))

    async def post_result(self, result: StageResult) -> dict[str, Any]:
        return dict(
            await self._request("POST", "/result", json=result.model_dump(mode="json", by_alias=True))
        )

    async def report_finding(self, finding: Finding) -> dict[str, Any]:
        return dict(await self._request("POST", "/findings", json=finding.model_dump(mode="json")))

    async def request_scope_change(self, paths: list[str], justification: str) -> dict[str, Any]:
        return dict(
            await self._request(
                "POST", "/scope-change", json={"paths": paths, "justification": justification}
            )
        )

    async def ask_human(self, text: str, options: list[str] | None = None) -> dict[str, Any]:
        return dict(await self._request("POST", "/question", json={"text": text, "options": options or []}))

    async def fetch_tools(self) -> list[dict[str, Any]]:
        """The catalogue tools THIS run may call, in MCP format."""
        payload = await self._request("GET", "/tools")
        tools = (payload or {}).get("tools", [])
        return [dict(t) for t in tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Calls a catalogue tool. The provider's key stays on the platform side."""
        return dict(await self._request("POST", f"/tools/{name}", json=arguments))

    async def fetch_context(self) -> ContextPack:
        return ContextPack.model_validate(await self._request("GET", "/context"))

    async def fetch_skills(self) -> list[dict[str, Any]]:
        """The files of the skills the `StageInput` names (ADR 0033); their digest is checked."""
        return list(await self._request("GET", "/skills") or [])

    async def fetch_inputs(self) -> list[dict[str, Any]]:
        """The declared inputs an earlier step produced (ADR 0049): name, filename, digest, size."""
        return list(await self._request("GET", "/inputs") or [])

    async def fetch_input_content(self, name: str) -> bytes:
        """The bytes of a declared input; the caller checks them against the listed digest."""
        response = await self._client.get(self._url(f"/inputs/{name}"))
        if response.status_code >= 400:
            raise InternalApiError(response.status_code, response.text[:400])
        return response.content

    async def put_output(self, name: str, type_: str, data: bytes, digest: str) -> dict[str, Any]:
        """Hands a deliverable of `.choregos/outputs/` to the platform, which hashes and stores it."""
        response = await self._client.put(
            self._url(f"/outputs/{name}"),
            params={"type": type_},
            content=data,
            headers={"X-Choregos-Digest": digest, "Content-Type": "application/octet-stream"},
        )
        if response.status_code >= 400:
            raise InternalApiError(response.status_code, response.text[:400])
        return dict(response.json())

    async def fetch_knowledge(self) -> list[dict[str, Any]]:
        """The pinned documents of the run's `files` knowledge bases (ADR 0051); the digest is checked."""
        return list(await self._request("GET", "/knowledge") or [])

    async def search_knowledge(
        self, query: str, k: int = 5, knowledge_base: str | None = None
    ) -> dict[str, Any]:
        """Hybrid search of the run's `index` knowledge bases (ADR 0051): citations and the mode."""
        return dict(
            await self._request(
                "POST",
                "/knowledge/search",
                json={"query": query, "k": k, "knowledge_base": knowledge_base},
            )
        )

    async def fetch_ticket(self) -> dict[str, Any]:
        return dict(await self._request("GET", "/ticket"))

    async def fetch_git_token(self) -> str | None:
        """The run's git token (S22-07): its repository only, one hour; `None` without App or off GitHub."""
        payload = await self._request("POST", "/git-token")
        token = (payload or {}).get("token")
        return str(token) if token else None

    async def fetch_ci_logs(self, tail: int = 500) -> str:
        payload = await self._request("GET", "/ci-logs", params={"tail": tail})
        return str((payload or {}).get("logs", ""))

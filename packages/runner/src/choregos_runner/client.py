"""Client de l'API interne : le seul canal de sortie du runner."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from choregos_contracts import ContextPack, Finding, StageInput, StageResult


class InternalApiError(RuntimeError):
    """L'API interne a refusé un appel."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"API interne : {status_code} — {detail}")
        self.status_code = status_code
        self.detail = detail


class InternalClient:
    """Appels au `/internal` de l'API, authentifiés par le jeton de run."""

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
        """Journal ACP en lot ; l'API déduplique par `seq`, donc un rejeu est sans effet."""
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

    async def fetch_context(self) -> ContextPack:
        return ContextPack.model_validate(await self._request("GET", "/context"))

    async def fetch_ticket(self) -> dict[str, Any]:
        return dict(await self._request("GET", "/ticket"))

    async def fetch_ci_logs(self, tail: int = 500) -> str:
        payload = await self._request("GET", "/ci-logs", params={"tail": tail})
        return str((payload or {}).get("logs", ""))

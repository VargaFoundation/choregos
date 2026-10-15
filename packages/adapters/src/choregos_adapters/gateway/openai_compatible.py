# SPDX-License-Identifier: Apache-2.0
"""A gateway that only speaks the OpenAI API (`/v1/models`, `/v1/chat/completions`).

Portkey, Kong AI Gateway, Envoy AI Gateway, OpenRouter, a vLLM router, Azure API Management...
all expose that surface. It gives Choregos discovery (`list_models`) and nothing else:

- **no virtual keys**: every run receives the same configured `run_key`. The operator must scope
  it at the gateway (a consumer with its own token rate limit), which is why it is a different
  secret from the `api_key` used for discovery;
- **no hard budget, no measured cost**: `spend` is an empty `Spend`, which means "not measured",
  not "free". The budget of a step is then held by Choregos alone (the reservation, turns and
  minutes), never by the gateway.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from choregos_core.domain import GatewayModel, Spend, VirtualKey

from ..errors import UpstreamError


class OpenAiCompatibleGateway:
    #: Discovery only: see the module docstring for what is lost.
    capabilities: frozenset[str] = frozenset({"list_models"})

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        run_key: str = "",
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.run_key = run_key
        self.headers = dict(headers or {})
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_models(self) -> list[GatewayModel]:
        headers = dict(self.headers)
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = await self._client.get(f"{self.base_url}/models", headers=headers)
        if response.status_code >= 400:
            raise UpstreamError(
                "openai-compatible",
                f"GET /models → {response.status_code} : {response.text[:300]}",
                status_code=response.status_code,
            )
        models: list[GatewayModel] = []
        for item in (response.json() or {}).get("data", []):
            name = str(item.get("id") or "")
            if name:
                models.append(
                    GatewayModel(
                        model_name=name,
                        litellm_model=name,
                        provider=str(item.get("owned_by") or ""),
                        owned_by=str(item.get("owned_by") or ""),
                    )
                )
        return models

    async def mint_key(
        self,
        metadata: dict[str, Any],
        budget_usd: float,
        ttl_s: int,
        models: list[str],
        team_id: str | None = None,
    ) -> VirtualKey:
        # UNIQUE per run: the platform stores each key under a unique `key_id` (same reason as
        # `DirectGateway`); the credential itself is the shared `run_key`.
        run = str(metadata.get("run_id") or uuid4())
        return VirtualKey(
            key=self.run_key,
            key_id=f"oai:{run}",
            budget_usd=budget_usd,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
            models=models,
            metadata=dict(metadata),
        )

    async def ensure_team(self, team_id: str, budget_usd: float, duration: str = "1d") -> str:
        return team_id

    async def spend(self, key_id: str) -> Spend:
        return Spend()

    async def revoke(self, key_id: str) -> None:
        return None

    async def test(self) -> dict[str, Any]:
        models = await self.list_models()
        return {"ok": bool(models), "models": len(models)}

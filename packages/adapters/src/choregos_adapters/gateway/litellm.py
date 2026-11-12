# SPDX-License-Identifier: Apache-2.0
"""The LiteLLM GatewayAdapter: one virtual key per run, the cost counted at the source.

The budget is a **hard cap** set on the key: when it is reached, LiteLLM refuses, the agent
receives an error, and the runner ends `failed(reason=budget)`. Nobody needs to trust the agent
about its consumption.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import structlog
from choregos_core.domain import GatewayModel, Spend, VirtualKey, utcnow
from choregos_core.models import DECISION_MODE

from ..base import GATEWAY_CAPABILITIES
from ..errors import UpstreamError

logger = structlog.get_logger("choregos.gateway.litellm")


class LiteLlmGateway:
    """Client of the LiteLLM proxy (keys, spend, model catalogue)."""

    capabilities: frozenset[str] = GATEWAY_CAPABILITIES

    def __init__(
        self,
        base_url: str,
        master_key: str,
        *,
        team_id: str | None = None,
        internal_prices: dict[str, float] | None = None,
        enterprise_tags: bool = False,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.master_key = master_key
        self.team_id = team_id
        # Key `tags` are a LiteLLM **Enterprise** feature: sent to an open-source proxy, they make
        # the mint fail with a 403. Attribution does not depend on them (`metadata` already carries
        # the project and the run), so they stay optional.
        self.enterprise_tags = enterprise_tags
        # Internal prices (EUR per M tokens) of the local models, to stay comparable (§4.2).
        self.internal_prices = internal_prices or {}
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(
            method,
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.master_key}"},
            **kwargs,
        )
        if response.status_code >= 400:
            raise UpstreamError(
                "litellm",
                f"{method} {path} → {response.status_code} : {response.text[:300]}",
                status_code=response.status_code,
            )
        return response.json() if response.content else {}

    async def mint_key(
        self,
        metadata: dict[str, Any],
        budget_usd: float,
        ttl_s: int,
        models: list[str],
        team_id: str | None = None,
    ) -> VirtualKey:
        payload: dict[str, Any] = {
            "max_budget": budget_usd,
            "duration": f"{max(1, ttl_s // 60)}m",
            "models": models,
            "metadata": metadata,
            "key_alias": f"run-{metadata.get('run_id', '')}"[:64],
        }
        if self.enterprise_tags:
            payload["tags"] = [
                f"project:{metadata.get('project', '')}",
                f"run:{metadata.get('run_id', '')}",
            ]
        # The project's team (S27-10) wins over the gateway-wide one: its budget is the project's day.
        if team_id or self.team_id:
            payload["team_id"] = team_id or self.team_id
        data = await self._mint(payload)
        return VirtualKey(
            key=data["key"],
            key_id=data.get("token") or data.get("key_name") or data["key"][-12:],
            budget_usd=budget_usd,
            expires_at=utcnow() + timedelta(seconds=ttl_s),
            models=list(models),
            metadata=dict(metadata),
        )

    async def _mint(self, payload: dict[str, Any]) -> Any:
        """Mints a key, surviving an alias that is already taken.

        LiteLLM requires `key_alias` values that are unique **for life**. A `run_id` is
        deterministic: if an attempt minted its key and then failed before recording it, the
        retry of the same run would see its key refused with a 400 and the ticket would stay
        blocked. The alias is only an operating convenience: the call starts again without it
        rather than immobilise a run.
        """
        try:
            return await self._request("POST", "/key/generate", json=payload)
        except UpstreamError as exc:
            taken = exc.status_code == 400 and "alias" in str(exc).lower()
            if not taken:
                raise
            logger.warning("key alias already taken, minting without alias", alias=payload.get("key_alias"))
            return await self._request(
                "POST", "/key/generate", json={k: v for k, v in payload.items() if k != "key_alias"}
            )

    async def spend(self, key_id: str) -> Spend:
        """The key's real spend: **the** source of truth for a run's cost."""
        info = await self._request("GET", "/key/info", params={"key": key_id})
        details = info.get("info", info)
        total = float(details.get("spend", 0.0))
        logs: list[dict[str, Any]] = []
        try:
            logs = list(await self._request("GET", "/spend/logs", params={"api_key": key_id}))
        except UpstreamError:
            logs = []
        tokens_in = sum(int(entry.get("prompt_tokens", 0)) for entry in logs)
        tokens_out = sum(int(entry.get("completion_tokens", 0)) for entry in logs)
        cached = sum(_cache_read_tokens(entry) for entry in logs)
        models = sorted({str(entry.get("model", "")) for entry in logs if entry.get("model")})
        if not total and logs:
            total = sum(float(entry.get("spend", 0.0)) for entry in logs)
        return Spend(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_cached=cached,
            cost_usd=round(total, 6),
            requests=len(logs),
            models_used=models,
        )

    async def revoke(self, key_id: str) -> None:
        try:
            await self._request("POST", "/key/delete", json={"keys": [key_id]})
        except UpstreamError as exc:
            if exc.status_code not in {404, 400}:  # already expired: that is the wanted result
                raise

    async def list_models(self) -> list[GatewayModel]:
        data = await self._request("GET", "/model/info")
        models: list[GatewayModel] = []
        for entry in data.get("data", []):
            info = entry.get("model_info", {}) or {}
            params = entry.get("litellm_params", {}) or {}
            name = str(entry.get("model_name", ""))
            litellm_model = str(params.get("model", name))
            mode = str(info.get("mode") or "chat")
            models.append(
                GatewayModel(
                    model_name=name,
                    litellm_model=litellm_model,
                    provider=str(info.get("litellm_provider", litellm_model.split("/", maxsplit=1)[0])),
                    input_cost_per_1k=_per_1k(info.get("input_cost_per_token")),
                    output_cost_per_1k=_per_1k(info.get("output_cost_per_token")),
                    input_audio_cost_per_1k=_per_1k(info.get("input_cost_per_audio_token")),
                    output_audio_cost_per_1k=_per_1k(info.get("output_cost_per_audio_token")),
                    cached_input_cost_per_1k=_per_1k(info.get("cache_read_input_token_cost")),
                    max_input_tokens=info.get("max_input_tokens"),
                    # A decision model answers typed questions; it never calls a tool (ADR 0121).
                    supports_tool_calling=mode != DECISION_MODE
                    and _unless_denied(info.get("supports_function_calling")),
                    supports_vision=bool(info.get("supports_vision", False)),
                    supports_reasoning=_flag(info.get("supports_reasoning")),
                    mode=mode,
                    internal_price=name in self.internal_prices,
                )
            )
        for name, price in self.internal_prices.items():
            if not any(model.model_name == name for model in models):
                models.append(
                    GatewayModel(
                        model_name=name,
                        litellm_model=name,
                        provider="local",
                        input_cost_per_1k=price / 1000,
                        output_cost_per_1k=price / 1000,
                        internal_price=True,
                    )
                )
        return models

    async def ensure_team(self, team_id: str, budget_usd: float, duration: str = "1d") -> str:
        """The team of a project, with its budget per `duration` (S27-10, ADR 0047). Idempotent: a
        team that exists gets its budget updated, so a policy change reaches the gateway."""
        body = {
            "team_id": team_id,
            "team_alias": team_id,
            "max_budget": budget_usd,
            "budget_duration": duration,
        }
        try:
            await self._request("POST", "/team/new", json=body)
        except UpstreamError as exc:
            if exc.status_code not in {400, 409}:
                raise
            await self._request(
                "POST",
                "/team/update",
                json={"team_id": team_id, "max_budget": budget_usd, "budget_duration": duration},
            )
        return team_id

    async def create_team(self, name: str, budget_usd: float) -> str:
        """A project's LiteLLM team, with its budget (provisioning step)."""
        data = await self._request(
            "POST", "/team/new", json={"team_alias": name, "max_budget": budget_usd, "budget_duration": "30d"}
        )
        return str(data.get("team_id", name))

    async def test(self) -> dict[str, Any]:
        models = await self.list_models()
        return {"ok": bool(models), "models": len(models)}


def _flag(value: Any) -> bool | None:
    """A capability the gateway states either way; `None` when it says nothing, so the console shows
    nothing rather than a guess (a missing flag is not a denial)."""
    return value if isinstance(value, bool) else None


def _unless_denied(flag: Any) -> bool:
    """A capability LiteLLM does not know is not a capability it denies.

    LiteLLM sends `supports_function_calling: null` for a model it has no metadata for, such as an
    `openai/<deployment>` alias on the dev tenant. `bool(info.get(key, True))` turned that null into
    False: every stage was refused with "does not support tool calling" (S22-42). Only an explicit
    false denies it."""
    return flag is not False


def _per_1k(per_token: Any) -> float | None:
    if per_token in (None, ""):
        return None
    return round(float(per_token) * 1000, 8)


def _cache_read_tokens(entry: dict[str, Any]) -> int:
    """The prompt tokens a spend log entry read from the provider's cache. LiteLLM writes them in
    the request's usage (`metadata.usage_object`), not at the top of the entry (S37-16): Anthropic's
    `cache_read_input_tokens`, else OpenAI's `prompt_tokens_details.cached_tokens`."""
    if entry.get("cache_read_input_tokens"):
        return int(entry["cache_read_input_tokens"])
    usage = (entry.get("metadata") or {}).get("usage_object") or {}
    if usage.get("cache_read_input_tokens"):
        return int(usage["cache_read_input_tokens"])
    return int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)

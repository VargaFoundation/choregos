# SPDX-License-Identifier: Apache-2.0
"""In-memory model gateway: virtual keys with a hard cap and simulated spend."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from choregos_core.domain import GatewayModel, Spend, VirtualKey, utcnow

from ..base import GATEWAY_CAPABILITIES

DEFAULT_MODELS = [
    GatewayModel(
        model_name="platform/strong",
        litellm_model="anthropic/claude-opus-5",
        provider="anthropic",
        input_cost_per_1k=0.015,
        output_cost_per_1k=0.075,
        max_input_tokens=200_000,
    ),
    GatewayModel(
        model_name="platform/standard",
        litellm_model="anthropic/claude-sonnet-5",
        provider="anthropic",
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.015,
        max_input_tokens=200_000,
    ),
    GatewayModel(
        model_name="platform/cheap",
        litellm_model="anthropic/claude-haiku-4-5",
        provider="anthropic",
        input_cost_per_1k=0.0008,
        output_cost_per_1k=0.004,
        max_input_tokens=200_000,
    ),
    GatewayModel(
        model_name="platform/embed",
        litellm_model="openai/text-embedding-3-large",
        provider="openai",
        input_cost_per_1k=0.00013,
        supports_tool_calling=False,
    ),
]


class FakeGateway:
    """Test gateway: `record_usage()` simulates requests, the cap cuts off like LiteLLM."""

    def __init__(
        self,
        models: list[GatewayModel] | None = None,
        *,
        auto_usage: bool = False,
        capabilities: frozenset[str] | None = None,
    ) -> None:
        # Scriptable down to any subset: a test of a platform behaviour that depends on what
        # the gateway cannot do (no hard budget, no spend readback) declares just that.
        self.capabilities = GATEWAY_CAPABILITIES if capabilities is None else frozenset(capabilities)
        self.models = models or list(DEFAULT_MODELS)
        # `auto_usage` simulates an agent's consumption: useful for the demo and for screenshots,
        # useless (and misleading) for cost tests.
        self.auto_usage = auto_usage
        self.keys: dict[str, VirtualKey] = {}
        self.spends: dict[str, Spend] = {}
        self.revoked: list[str] = []
        #: The teams (a project's day budget) and what each has spent, as LiteLLM keeps them (S27-10).
        self.teams: dict[str, dict[str, Any]] = {}
        self.team_spend: dict[str, float] = {}
        self.key_team: dict[str, str] = {}
        self._minted = 0

    async def ensure_team(self, team_id: str, budget_usd: float, duration: str = "1d") -> str:
        """Idempotent: a team that exists keeps its spend and gets its budget updated."""
        self.teams[team_id] = {"budget_usd": budget_usd, "duration": duration}
        self.team_spend.setdefault(team_id, 0.0)
        return team_id

    async def mint_key(
        self,
        metadata: dict[str, Any],
        budget_usd: float,
        ttl_s: int,
        models: list[str],
        team_id: str | None = None,
    ) -> VirtualKey:
        self._minted += 1
        if team_id is not None:
            self.key_team[f"key-{self._minted}"] = team_id
        key_id = f"key-{self._minted}"
        key = VirtualKey(
            key=f"sk-fake-{key_id}",
            key_id=key_id,
            budget_usd=budget_usd,
            expires_at=utcnow() + timedelta(seconds=ttl_s),
            models=list(models),
            metadata=dict(metadata),
        )
        self.keys[key_id] = key
        self.spends[key_id] = Spend()
        if self.auto_usage:
            model = models[0] if models else "platform/standard"
            self.record_usage(key_id, tokens_in=120_000, tokens_out=9_000, cached=60_000, model=model)
        return key

    def record_usage(
        self, key_id: str, tokens_in: int, tokens_out: int, cached: int = 0, model: str = "platform/standard"
    ) -> Spend:
        """Simulates requests; refuses beyond the cap, like the real gateway."""
        gateway_model = next((m for m in self.models if model in {m.model_name, m.litellm_model}), None)
        cost = 0.0
        if gateway_model:
            cost = (tokens_in - cached) / 1000 * (gateway_model.input_cost_per_1k or 0)
            cost += cached / 1000 * (gateway_model.input_cost_per_1k or 0) * 0.1
            cost += tokens_out / 1000 * (gateway_model.output_cost_per_1k or 0)
        current = self.spends.get(key_id, Spend())
        key = self.keys[key_id]
        if current.cost_usd + cost > key.budget_usd:
            # LiteLLM's own words: the runner recognises the refusal by them (S27-01).
            raise BudgetExceededError(
                f"Budget has been exceeded! Current cost: {current.cost_usd + cost:.4f}, "
                f"Max budget: {key.budget_usd:.4f} (key {key_id})"
            )
        team = self.key_team.get(key_id)
        if team is not None and team in self.teams:
            cap = float(self.teams[team]["budget_usd"])
            if self.team_spend[team] + cost > cap:
                raise BudgetExceededError(
                    f"Budget has been exceeded! Current cost: {self.team_spend[team] + cost:.4f}, "
                    f"Max budget: {cap:.4f} (team {team})"
                )
            self.team_spend[team] += cost
        self.spends[key_id] = current + Spend(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_cached=cached,
            cost_usd=cost,
            requests=1,
            models_used=[model],
        )
        return self.spends[key_id]

    async def spend(self, key_id: str) -> Spend:
        return self.spends.get(key_id, Spend())

    async def revoke(self, key_id: str) -> None:
        self.revoked.append(key_id)
        self.keys.pop(key_id, None)

    async def list_models(self) -> list[GatewayModel]:
        return list(self.models)

    async def test(self) -> dict[str, Any]:
        return {"ok": bool(self.models), "models": len(self.models)}


class BudgetExceededError(RuntimeError):
    """The virtual key's hard cap cut the request."""


BudgetExceeded = BudgetExceededError  # historical alias

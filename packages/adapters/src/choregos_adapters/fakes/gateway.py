"""Gateway de modèles en mémoire : clés virtuelles à plafond dur et dépenses simulées."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from choregos_core.domain import GatewayModel, Spend, VirtualKey, utcnow

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
    """Gateway de test : `record_usage()` simule des requêtes, le plafond coupe comme LiteLLM."""

    def __init__(self, models: list[GatewayModel] | None = None) -> None:
        self.models = models or list(DEFAULT_MODELS)
        self.keys: dict[str, VirtualKey] = {}
        self.spends: dict[str, Spend] = {}
        self.revoked: list[str] = []

    async def mint_key(
        self, metadata: dict[str, Any], budget_usd: float, ttl_s: int, models: list[str]
    ) -> VirtualKey:
        key_id = f"key-{len(self.keys) + 1}"
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
        return key

    def record_usage(
        self, key_id: str, tokens_in: int, tokens_out: int, cached: int = 0, model: str = "platform/standard"
    ) -> Spend:
        """Simule des requêtes ; refuse au-delà du plafond, comme le vrai gateway."""
        gateway_model = next((m for m in self.models if model in {m.model_name, m.litellm_model}), None)
        cost = 0.0
        if gateway_model:
            cost = (tokens_in - cached) / 1000 * (gateway_model.input_cost_per_1k or 0)
            cost += cached / 1000 * (gateway_model.input_cost_per_1k or 0) * 0.1
            cost += tokens_out / 1000 * (gateway_model.output_cost_per_1k or 0)
        current = self.spends.get(key_id, Spend())
        key = self.keys[key_id]
        if current.cost_usd + cost > key.budget_usd:
            raise BudgetExceededError(
                f"budget dépassé pour {key_id} : {current.cost_usd + cost:.2f} > {key.budget_usd:.2f}"
            )
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


class BudgetExceededError(RuntimeError):
    """Le plafond dur de la clé virtuelle a coupé la requête."""


BudgetExceeded = BudgetExceededError  # alias historique

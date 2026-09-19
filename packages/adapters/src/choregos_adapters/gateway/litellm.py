"""GatewayAdapter LiteLLM : une clé virtuelle par run, le coût compté à la source.

Le budget est un **plafond dur** posé sur la clé : quand il est atteint, LiteLLM refuse,
l'agent reçoit une erreur, et le runner termine `failed(reason=budget)`. Personne n'a
besoin de faire confiance à l'agent sur sa consommation.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import structlog
from choregos_core.domain import GatewayModel, Spend, VirtualKey, utcnow

from ..errors import UpstreamError

logger = structlog.get_logger("choregos.gateway.litellm")


class LiteLlmGateway:
    """Client du proxy LiteLLM (clés, dépenses, catalogue de modèles)."""

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
        # Les `tags` de clé sont une fonction LiteLLM **Enterprise** : envoyées à un proxy
        # open-source, elles font échouer le mint en 403. L'attribution ne dépend pas d'elles —
        # `metadata` porte déjà le projet et le run — donc elles restent optionnelles.
        self.enterprise_tags = enterprise_tags
        # Prix internes (€/M tokens) des modèles locaux, pour rester comparable (§4.2).
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
        self, metadata: dict[str, Any], budget_usd: float, ttl_s: int, models: list[str]
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
        if self.team_id:
            payload["team_id"] = self.team_id
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
        """Mint une clé, en survivant à un alias déjà pris.

        LiteLLM exige des `key_alias` uniques **à vie**. Or un `run_id` est déterministe : si
        une tentative a minté sa clé puis échoué avant de l'enregistrer, la reprise du même run
        se verrait refuser sa clé en 400 et le ticket resterait bloqué. L'alias n'est qu'un
        confort d'exploitation — on repart sans lui plutôt que d'immobiliser un run.
        """
        try:
            return await self._request("POST", "/key/generate", json=payload)
        except UpstreamError as exc:
            taken = exc.status_code == 400 and "alias" in str(exc).lower()
            if not taken:
                raise
            logger.warning("alias de clé déjà pris, mint sans alias", alias=payload.get("key_alias"))
            return await self._request(
                "POST", "/key/generate", json={k: v for k, v in payload.items() if k != "key_alias"}
            )

    async def spend(self, key_id: str) -> Spend:
        """Dépense réelle de la clé : c'est **la** source de vérité du coût d'un run."""
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
        cached = sum(int(entry.get("cache_read_input_tokens") or 0) for entry in logs)
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
            if exc.status_code not in {404, 400}:  # déjà expirée : c'est le résultat voulu
                raise

    async def list_models(self) -> list[GatewayModel]:
        data = await self._request("GET", "/model/info")
        models: list[GatewayModel] = []
        for entry in data.get("data", []):
            info = entry.get("model_info", {}) or {}
            params = entry.get("litellm_params", {}) or {}
            name = str(entry.get("model_name", ""))
            litellm_model = str(params.get("model", name))
            models.append(
                GatewayModel(
                    model_name=name,
                    litellm_model=litellm_model,
                    provider=str(info.get("litellm_provider", litellm_model.split("/", maxsplit=1)[0])),
                    input_cost_per_1k=_per_1k(info.get("input_cost_per_token")),
                    output_cost_per_1k=_per_1k(info.get("output_cost_per_token")),
                    max_input_tokens=info.get("max_input_tokens"),
                    supports_tool_calling=bool(info.get("supports_function_calling", True)),
                    supports_vision=bool(info.get("supports_vision", False)),
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

    async def create_team(self, name: str, budget_usd: float) -> str:
        """Équipe LiteLLM d'un projet, avec son budget (étape de provisioning)."""
        data = await self._request(
            "POST", "/team/new", json={"team_alias": name, "max_budget": budget_usd, "budget_duration": "30d"}
        )
        return str(data.get("team_id", name))

    async def test(self) -> dict[str, Any]:
        models = await self.list_models()
        return {"ok": bool(models), "models": len(models)}


def _per_1k(per_token: Any) -> float | None:
    if per_token in (None, ""):
        return None
    return round(float(per_token) * 1000, 8)

"""The LiteLLM gateway for real: virtual keys, budget, cost, and Anthropic format (S4).

The plan rests two guarantees on this proxy: a run's cost is **measured at the gateway** (never
declared by the agent) and the budget is a **hard ceiling** (the key refuses beyond it). No fake
can prove that. This one checks it against a real LiteLLM:

    docker run -d --name litellm -p 4000:4000 -v …/litellm.yaml:/app/config.yaml:ro \
      -e LITELLM_MASTER_KEY=sk-… -e DATABASE_URL=postgresql://… \
      ghcr.io/berriai/litellm:main-stable --config /app/config.yaml --port 4000

    CHOREGOS_LIVE_LITELLM_URL=http://127.0.0.1:4000 CHOREGOS_LIVE_LITELLM_KEY=sk-… \
    uv run pytest tests/live -m live
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from choregos_adapters.gateway.litellm import LiteLlmGateway
from choregos_core import ModelResolver
from choregos_core.domain import GatewayModel

from .conftest import require

pytestmark = pytest.mark.live

MODEL = "platform/standard"
# An alias whose price LiteLLM knows: needed to check the cost measurement.
PRICED_MODEL = "platform/cheap"

# LiteLLM requires key aliases to be unique for life: without a marker, a second run of the
# test would have its keys refused for the wrong reasons.
RUN = uuid4().hex[:8]


@pytest.fixture
async def gateway() -> AsyncIterator[LiteLlmGateway]:
    env = require("CHOREGOS_LIVE_LITELLM_URL", "CHOREGOS_LIVE_LITELLM_KEY")
    adapter = LiteLlmGateway(
        base_url=env["CHOREGOS_LIVE_LITELLM_URL"],
        master_key=env["CHOREGOS_LIVE_LITELLM_KEY"],
    )
    try:
        yield adapter
    finally:
        await adapter.aclose()


@pytest.fixture
def base_url() -> str:
    return require("CHOREGOS_LIVE_LITELLM_URL")["CHOREGOS_LIVE_LITELLM_URL"]


async def test_le_proxy_expose_les_alias_de_la_plateforme(base_url: str, gateway: LiteLlmGateway) -> None:
    """Agents never see a provider name, only a platform alias."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{base_url}/v1/models", headers={"Authorization": f"Bearer {gateway.master_key}"}
        )
    assert response.status_code == 200
    names = {entry["id"] for entry in response.json()["data"]}
    assert MODEL in names, names


async def test_une_cle_de_run_est_mintee_bornee_et_revocable(gateway: LiteLlmGateway) -> None:
    key = await gateway.mint_key(
        {"project": "billing-api", "run_id": f"live-{RUN}-1", "actor": "dev"},
        budget_usd=0.5,
        ttl_s=900,
        models=[MODEL],
    )
    assert key.key.startswith("sk-")
    assert key.budget_usd == 0.5
    assert key.models == [MODEL]

    # A fresh key has spent nothing — and it is the gateway that says so, not us.
    spent = await gateway.spend(key.key)
    assert spent.cost_usd == 0.0
    assert spent.requests == 0

    await gateway.revoke(key.key)


async def test_une_cle_de_run_ne_peut_appeler_que_ses_modeles(gateway: LiteLlmGateway, base_url: str) -> None:
    """The model scope is a bound of the proxy, not an instruction in a prompt."""
    key = await gateway.mint_key(
        {"project": "billing-api", "run_id": f"live-{RUN}-2"},
        budget_usd=0.5,
        ttl_s=900,
        models=[MODEL],
    )
    async with httpx.AsyncClient(timeout=30) as client:
        autorise = await client.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key.key}"},
            json={"model": MODEL, "messages": [{"role": "user", "content": "hello"}]},
        )
        refuse = await client.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key.key}"},
            json={"model": "platform/cheap", "messages": [{"role": "user", "content": "hello"}]},
        )
    assert autorise.status_code == 200, autorise.text[:300]
    assert refuse.status_code >= 400, "a model out of scope must be refused by the gateway"

    await gateway.revoke(key.key)


async def test_le_cout_est_mesure_a_la_passerelle(gateway: LiteLlmGateway, base_url: str) -> None:
    """The S4 rule: a run's cost comes from the gateway, never from the agent.

    On a priced model (`platform/cheap` → gpt-4o-mini): an alias whose price the proxy does not
    know bills 0, which would prove nothing. The gateway writes its spend logs asynchronously,
    hence the bounded wait.
    """
    import asyncio

    key = await gateway.mint_key(
        {"project": "billing-api", "run_id": f"live-{RUN}-3"},
        budget_usd=1.0,
        ttl_s=900,
        models=[PRICED_MODEL],
    )
    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(2):
            response = await client.post(
                f"{base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key.key}"},
                json={
                    "model": PRICED_MODEL,
                    "messages": [{"role": "user", "content": "count to three"}],
                },
            )
            assert response.status_code == 200, response.text[:200]

    spent = await gateway.spend(key.key)
    for _ in range(15):
        if spent.cost_usd > 0:
            break
        await asyncio.sleep(2)
        spent = await gateway.spend(key.key)

    assert spent.cost_usd > 0, "two billed calls must be visible from the gateway"
    assert spent.tokens_in > 0 and spent.tokens_out > 0
    assert spent.requests >= 2

    await gateway.revoke(key.key)


async def test_le_budget_est_un_plafond_dur(gateway: LiteLlmGateway, base_url: str) -> None:
    """A tiny budget must end up refusing — it is a mechanism, not an instruction."""
    key = await gateway.mint_key(
        {"project": "billing-api", "run_id": f"live-{RUN}-4"},
        budget_usd=0.0001,
        ttl_s=900,
        models=[MODEL],
    )
    refus = None
    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(6):
            response = await client.post(
                f"{base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key.key}"},
                json={"model": MODEL, "messages": [{"role": "user", "content": "x" * 400}]},
            )
            if response.status_code >= 400:
                refus = response
                break
    assert refus is not None, "the budget was never enforced"
    assert "budget" in refus.text.lower(), refus.text[:300]

    await gateway.revoke(key.key)


async def test_une_cle_revoquee_ne_passe_plus(gateway: LiteLlmGateway, base_url: str) -> None:
    key = await gateway.mint_key(
        {"project": "billing-api", "run_id": f"live-{RUN}-5"},
        budget_usd=0.5,
        ttl_s=900,
        models=[MODEL],
    )
    await gateway.revoke(key.key)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key.key}"},
            json={"model": MODEL, "messages": [{"role": "user", "content": "hello"}]},
        )
    assert response.status_code >= 400, "a revoked key is a dead key"


# ───────────────────── Anthropic format: the S4-05 gap ─────────────────────


async def test_le_format_anthropic_passe_par_le_meme_alias(gateway: LiteLlmGateway, base_url: str) -> None:
    """`claude-code` only speaks `/v1/messages`: the platform alias must answer there."""
    key = await gateway.mint_key(
        {"project": "billing-api", "run_id": f"live-{RUN}-6"},
        budget_usd=0.5,
        ttl_s=900,
        models=[MODEL],
    )
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/v1/messages",
            headers={
                "Authorization": f"Bearer {key.key}",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "prompt-caching-2024-07-31",
            },
            json={
                "model": MODEL,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert response.status_code == 200, response.text[:400]
    payload = response.json()
    assert payload["type"] == "message", "the response is in Anthropic format, not OpenAI"
    assert payload["content"][0]["type"] == "text"
    assert "usage" in payload and payload["usage"]["input_tokens"] > 0

    await gateway.revoke(key.key)


async def test_l_environnement_que_recoit_claude_code_fonctionne(
    gateway: LiteLlmGateway, base_url: str
) -> None:
    """The missing proof: the variables passed to the runner lead to a proxy that answers.

    We take exactly what `prepare_stage` would put in the container — a resolved `ModelRef`
    then `backend.model_env()` — and use it to call. If a variable is misnamed or points to the
    wrong place, the call fails here rather than on the first real run.
    """
    from choregos_runner.backends import get_backend

    key = await gateway.mint_key(
        {"project": "billing-api", "run_id": f"live-{RUN}-7"},
        budget_usd=0.5,
        ttl_s=900,
        models=[MODEL],
    )
    resolver = ModelResolver(
        gateway_url=base_url,
        gateway_models=[GatewayModel(model_name=MODEL, litellm_model="anthropic/claude-sonnet-5")],
    )
    resolved = resolver.resolve(MODEL, backend="claude-code")
    assert resolved.api_format == "anthropic"

    env = get_backend("claude-code").model_env(resolved.to_ref(), key.key)
    base = env.get("ANTHROPIC_BASE_URL", "")
    token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY", "")
    model = env.get("ANTHROPIC_MODEL", MODEL)
    assert base and token, env

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base.rstrip('/')}/v1/messages",
            headers={"Authorization": f"Bearer {token}", "anthropic-version": "2023-06-01"},
            json={"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": "ping"}]},
        )
    assert response.status_code == 200, f"{base} → {response.text[:300]}"
    assert response.json()["type"] == "message"

    await gateway.revoke(key.key)

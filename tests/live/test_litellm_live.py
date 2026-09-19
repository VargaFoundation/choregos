"""La passerelle LiteLLM en vrai : clés virtuelles, budget, coût, et format Anthropic (S4).

Le plan fait reposer deux garanties sur ce proxy : le coût d'un run est **mesuré à la
passerelle** (jamais déclaré par l'agent) et le budget est un **plafond dur** (la clé refuse
au-delà). Aucun fake ne peut prouver ça. Celui-ci le vérifie contre un vrai LiteLLM :

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
# Un alias dont LiteLLM connaît le tarif : nécessaire pour vérifier la mesure du coût.
PRICED_MODEL = "platform/cheap"

# LiteLLM exige des alias de clé uniques à vie : sans marqueur, une seconde exécution du
# test se verrait refuser ses clés pour de mauvaises raisons.
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
    """Les agents ne voient jamais un nom de fournisseur, seulement un alias de plateforme."""
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

    # Une clé fraîche n'a rien dépensé — et c'est la passerelle qui le dit, pas nous.
    spent = await gateway.spend(key.key)
    assert spent.cost_usd == 0.0
    assert spent.requests == 0

    await gateway.revoke(key.key)


async def test_une_cle_de_run_ne_peut_appeler_que_ses_modeles(gateway: LiteLlmGateway, base_url: str) -> None:
    """Le périmètre de modèles est une borne du proxy, pas une consigne dans un prompt."""
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
            json={"model": MODEL, "messages": [{"role": "user", "content": "bonjour"}]},
        )
        refuse = await client.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key.key}"},
            json={"model": "platform/cheap", "messages": [{"role": "user", "content": "bonjour"}]},
        )
    assert autorise.status_code == 200, autorise.text[:300]
    assert refuse.status_code >= 400, "un modèle hors périmètre doit être refusé par la passerelle"

    await gateway.revoke(key.key)


async def test_le_cout_est_mesure_a_la_passerelle(gateway: LiteLlmGateway, base_url: str) -> None:
    """La règle de S4 : le coût d'un run vient de la passerelle, jamais de l'agent.

    Sur un modèle tarifé (`platform/cheap` → gpt-4o-mini) : un alias dont le proxy ne connaît
    pas le prix facture 0, ce qui ne prouverait rien. La passerelle écrit ses journaux de
    dépense de façon asynchrone, d'où l'attente bornée.
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
                    "messages": [{"role": "user", "content": "compte jusqu'à trois"}],
                },
            )
            assert response.status_code == 200, response.text[:200]

    spent = await gateway.spend(key.key)
    for _ in range(15):
        if spent.cost_usd > 0:
            break
        await asyncio.sleep(2)
        spent = await gateway.spend(key.key)

    assert spent.cost_usd > 0, "deux appels facturés doivent se voir depuis la passerelle"
    assert spent.tokens_in > 0 and spent.tokens_out > 0
    assert spent.requests >= 2

    await gateway.revoke(key.key)


async def test_le_budget_est_un_plafond_dur(gateway: LiteLlmGateway, base_url: str) -> None:
    """Un budget minuscule doit finir par refuser — c'est un mécanisme, pas une consigne."""
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
    assert refus is not None, "le budget n'a jamais été opposé"
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
            json={"model": MODEL, "messages": [{"role": "user", "content": "bonjour"}]},
        )
    assert response.status_code >= 400, "une clé révoquée est une clé morte"


# ───────────────────── format Anthropic : le trou de S4-05 ─────────────────────


async def test_le_format_anthropic_passe_par_le_meme_alias(gateway: LiteLlmGateway, base_url: str) -> None:
    """`claude-code` ne parle que `/v1/messages` : l'alias de plateforme doit y répondre."""
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
                "messages": [{"role": "user", "content": "bonjour"}],
            },
        )
    assert response.status_code == 200, response.text[:400]
    payload = response.json()
    assert payload["type"] == "message", "la réponse est au format Anthropic, pas OpenAI"
    assert payload["content"][0]["type"] == "text"
    assert "usage" in payload and payload["usage"]["input_tokens"] > 0

    await gateway.revoke(key.key)


async def test_l_environnement_que_recoit_claude_code_fonctionne(
    gateway: LiteLlmGateway, base_url: str
) -> None:
    """La preuve qui manquait : les variables passées au runner mènent à un proxy qui répond.

    On prend exactement ce que `prepare_stage` mettrait dans le conteneur — `ModelRef` résolu
    puis `backend.model_env()` — et on s'en sert pour appeler. Si une variable est mal nommée
    ou pointe au mauvais endroit, l'appel échoue ici plutôt qu'au premier run réel.
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

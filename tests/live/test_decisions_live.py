"""A decision through the gateway, for real: LiteLLM 1.105.5 in front of Laya (S52-02, ADR 0121).

What no rendering proves: that LiteLLM 1.105.5's `typesafe` provider, pointed at Laya's Service,
reaches `laya-serve` with Laya's bearer and reads its answers back; that the chart's guard and span
callback let a `/v1/systemone` call through; that a run's virtual key carries the call; and that the
internal price lands in the spend log (ADR 0004). The stack is `tests/live/decisions/compose.yaml`:

    docker compose -p s5202 -f tests/live/decisions/compose.yaml up -d --build --wait
    CHOREGOS_LIVE_DECISIONS=1 uv run pytest tests/live/test_decisions_live.py -m live
    docker compose -p s5202 -f tests/live/decisions/compose.yaml down -v
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import httpx
import pytest

from .conftest import require

pytestmark = pytest.mark.live

GATEWAY = os.environ.get("CHOREGOS_LIVE_DECISIONS_GATEWAY_URL", "http://127.0.0.1:14410")
# The compose file's master key, a local-only placeholder (assembled, not a literal key).
MASTER_KEY = "-".join(("sk", "local", "decisions", "only"))
MODEL = "platform/decide"
# The internal price of `tests/live/decisions/litellm.yaml`, as the chart's default.
INPUT_COST_PER_TOKEN = 2e-08
RUN = uuid4().hex[:8]

# A French request on purpose: the checkpoint is the multilingual one.
STATE = (
    "Bonjour, je n'arrive plus à me connecter à mon compte depuis hier, "  # lang: fr-ok
    "et la facture de ce mois-ci compte deux fois le même abonnement."  # lang: fr-ok
)
QUESTIONS: dict[str, Any] = {
    "urgent": {"type": "noul", "instructions": "Must it be handled today?"},
    "route": {
        "type": "choice",
        "instructions": "Which team handles this request?",
        "criteria": {"billing": None, "tech": None, "other": None},
    },
}


@pytest.fixture
def client() -> httpx.Client:
    require("CHOREGOS_LIVE_DECISIONS")
    return httpx.Client(timeout=60)


def _eventually[T](probe: Callable[[], T | None], *, timeout_s: float, what: str) -> T:
    deadline = time.monotonic() + timeout_s
    while True:
        found = probe()
        if found is not None:
            return found
        if time.monotonic() > deadline:
            pytest.fail(f"{what}: nothing after {timeout_s:.0f} s")
        time.sleep(2)


def _master() -> dict[str, str]:
    return {"Authorization": f"Bearer {MASTER_KEY}"}


def _mint(client: httpx.Client, run: str, models: list[str]) -> str:
    response = client.post(
        f"{GATEWAY}/key/generate",
        headers=_master(),
        json={
            "models": models,
            "max_budget": 0.05,
            "duration": "15m",
            "key_alias": f"s5202-{RUN}-{run}",
            "metadata": {"project": "decisions-live", "run_id": f"s5202-{RUN}-{run}", "stage": "decide"},
        },
    )
    assert response.status_code == 200, response.text[:300]
    return str(response.json()["key"])


def test_a_decision_goes_through_the_gateway_and_is_accounted(client: httpx.Client) -> None:
    key = _mint(client, "decide", [MODEL])
    response = client.post(
        f"{GATEWAY}/v1/systemone",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": MODEL, "state": STATE, "questions": QUESTIONS},
    )
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    answers = body["answers"]
    assert set(answers) == {"urgent", "route"}, body

    # `noul`: a yes/no answered as the probability of yes.
    urgent = answers["urgent"]
    assert urgent["type"] == "noul"
    assert 0.0 <= urgent["noul"] <= 1.0
    # `choice`: one of the options offered, with a probability for each, summing to one.
    route = answers["route"]
    assert route["type"] == "choice"
    assert route["choice"] in {"billing", "tech", "other"}
    assert set(route["probabilities"]) == {"billing", "tech", "other"}
    assert all(0.0 <= p <= 1.0 for p in route["probabilities"].values())
    assert sum(route["probabilities"].values()) == pytest.approx(1.0, abs=0.01)
    assert 0.0 <= route["confidence"] <= 1.0
    assert body["usage"]["input_tokens"] > 0

    def logged() -> list[dict[str, Any]] | None:
        logs = client.get(f"{GATEWAY}/spend/logs", headers=_master(), params={"api_key": key})
        entries = logs.json() if logs.status_code == 200 else []
        return entries or None

    # LiteLLM writes its spend logs in batches: the bounded wait is for that, not for the model.
    (entry,) = _eventually(logged, timeout_s=90, what="the spend log of the decision")
    assert (entry["model_group"], entry["call_type"]) == (MODEL, "asystemone"), entry
    assert entry["custom_llm_provider"] == "typesafe", entry
    assert entry["prompt_tokens"] == body["usage"]["input_tokens"], entry
    # ADR 0004: the internal price, counted at the gateway, never zero.
    assert entry["spend"] == pytest.approx(body["usage"]["input_tokens"] * INPUT_COST_PER_TOKEN, rel=0.01)


def test_a_key_minted_for_the_agents_cannot_ask_for_a_decision(client: httpx.Client) -> None:
    """The model scope applies to the decision route as to the chat one."""
    key = _mint(client, "agent", ["platform/standard"])
    response = client.post(
        f"{GATEWAY}/v1/systemone",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": MODEL, "state": STATE, "questions": QUESTIONS},
    )
    assert response.status_code in {401, 403}, response.text[:300]


def test_the_catalogue_sync_reads_the_internal_price(client: httpx.Client) -> None:
    """`LiteLlmGateway.list_models` reads `/model/info`: the price must come back from there."""
    require("CHOREGOS_LIVE_DECISIONS")
    response = client.get(f"{GATEWAY}/model/info", headers=_master())
    assert response.status_code == 200, response.text[:300]
    (entry,) = [e for e in response.json()["data"] if e["model_name"] == MODEL]
    assert entry["model_info"]["input_cost_per_token"] == pytest.approx(INPUT_COST_PER_TOKEN)

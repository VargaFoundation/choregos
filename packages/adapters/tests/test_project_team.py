"""The gateway team of a project: its budget is the project's day (S27-10, ADR 0047)."""

from __future__ import annotations

import json

import respx
from choregos_adapters.fakes import BudgetExceededError, FakeGateway
from choregos_adapters.gateway.litellm import LiteLlmGateway
from httpx import Response

LITELLM = "http://litellm.test"


@respx.mock
async def test_litellm_the_key_joins_the_project_team() -> None:
    route = respx.post(f"{LITELLM}/key/generate").mock(return_value=Response(200, json={"key": "sk-t"}))
    await LiteLlmGateway(LITELLM, "sk", team_id="global").mint_key(
        {"run_id": "r-1"}, 1.0, 60, ["m"], team_id="choregos-p1"
    )
    assert json.loads(route.calls[0].request.content)["team_id"] == "choregos-p1"


@respx.mock
async def test_litellm_ensure_team_creates_then_updates() -> None:
    new = respx.post(f"{LITELLM}/team/new").mock(
        side_effect=[Response(200, json={"team_id": "choregos-p1"}), Response(400, json={"error": "exists"})]
    )
    update = respx.post(f"{LITELLM}/team/update").mock(return_value=Response(200, json={}))
    gateway = LiteLlmGateway(LITELLM, "sk")
    assert await gateway.ensure_team("choregos-p1", 50.0) == "choregos-p1"
    body = json.loads(new.calls[0].request.content)
    assert body["max_budget"] == 50.0 and body["budget_duration"] == "1d" and body["team_id"] == "choregos-p1"
    assert not update.called
    await gateway.ensure_team("choregos-p1", 40.0)  # exists: the budget is updated, not an error
    assert json.loads(update.calls[0].request.content)["max_budget"] == 40.0


async def test_the_fake_team_cuts_every_key_of_the_project() -> None:
    gateway = FakeGateway()
    await gateway.ensure_team("t", 0.5)
    first = await gateway.mint_key({"run_id": "a"}, 5.0, 60, ["platform/standard"], team_id="t")
    second = await gateway.mint_key({"run_id": "b"}, 5.0, 60, ["platform/standard"], team_id="t")
    gateway.record_usage(first.key_id, tokens_in=100_000, tokens_out=0)  # 0.30 $ of the team's 0.50 $
    try:
        gateway.record_usage(second.key_id, tokens_in=100_000, tokens_out=0)
    except BudgetExceededError as refusal:
        assert "team t" in str(refusal)
    else:
        raise AssertionError("the second key's call must be refused by the team")

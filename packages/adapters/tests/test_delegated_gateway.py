"""The LiteLLM gateway reached with a delegated service-account key (S82-05, ADR 0205).

The key is an `internal_user`'s: it mints, reads and deletes its own child keys and nothing else.
LiteLLM refuses a team (401/403), checks neither the models nor the duration of a child at creation,
and fills `max_budget` with 1000 when it is omitted: the platform sends all three, every time.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from tranibor_adapters import build
from tranibor_adapters.base import GATEWAY_CAPABILITIES
from tranibor_adapters.errors import ConfigurationError, UpstreamError
from tranibor_adapters.fakes import FakeGateway
from tranibor_adapters.gateway.litellm import LiteLlmGateway
from tranibor_adapters.gateway.project_team import ensure_project_team
from tranibor_core.llm_guardrail import GUARD_METADATA_KEY, GUARD_REDACT

LITELLM = "http://litellm.test"


def _delegated(**kwargs: object) -> LiteLlmGateway:
    return LiteLlmGateway(LITELLM, "sk-svc", key_mode="delegated", **kwargs)  # type: ignore[arg-type]


def test_a_delegated_gateway_has_no_teams_and_keeps_the_rest() -> None:
    assert _delegated().capabilities == GATEWAY_CAPABILITIES - {"teams"}
    assert "llm_guard" in _delegated().capabilities  # the shared gateway's guard keys on the metadata
    assert LiteLlmGateway(LITELLM, "k").capabilities == GATEWAY_CAPABILITIES


def test_an_unknown_key_mode_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="key mode"):
        LiteLlmGateway(LITELLM, "k", key_mode="root")


def test_the_mode_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    monkeypatch.setenv("CHOREGOS_GATEWAY_KEY_MODE", "delegated")
    gateway = build("gateway", "litellm", {"base_url": LITELLM, "master_key": "sk-svc"})
    assert "teams" not in gateway.capabilities
    monkeypatch.delenv("CHOREGOS_GATEWAY_KEY_MODE")
    assert "teams" in build("gateway", "litellm", {"base_url": LITELLM, "master_key": "k"}).capabilities


@respx.mock
async def test_a_delegated_key_is_minted_with_its_caps_and_no_team_nor_tag() -> None:
    route = respx.post(f"{LITELLM}/key/generate").mock(return_value=httpx.Response(200, json={"key": "sk-c"}))
    gateway = _delegated(team_id="global", enterprise_tags=True)
    metadata = {"run_id": "r-1", "project": "p", GUARD_METADATA_KEY: GUARD_REDACT}
    await gateway.mint_key(metadata, 1.5, 3600, ["choregos-standard"], team_id="choregos-p1")
    request = route.calls[0].request
    body = json.loads(request.content)
    assert request.headers["authorization"] == "Bearer sk-svc"
    assert body["max_budget"] == 1.5 and body["duration"] == "60m" and body["models"] == ["choregos-standard"]
    assert body["metadata"][GUARD_METADATA_KEY] == GUARD_REDACT
    assert "team_id" not in body and "tags" not in body


@respx.mock
async def test_the_master_mode_is_unchanged() -> None:
    route = respx.post(f"{LITELLM}/key/generate").mock(return_value=httpx.Response(200, json={"key": "sk-c"}))
    await LiteLlmGateway(LITELLM, "sk", team_id="global", enterprise_tags=True).mint_key(
        {"run_id": "r"}, 1.0, 60, ["m"], team_id="choregos-p1"
    )
    body = json.loads(route.calls[0].request.content)
    assert body["team_id"] == "choregos-p1" and body["tags"]


@pytest.mark.parametrize(("budget", "models"), [(0.0, ["m"]), (-1.0, ["m"]), (1.0, [])])
async def test_a_delegated_key_without_a_budget_or_models_is_never_sent(
    budget: float, models: list[str]
) -> None:
    """LiteLLM would fill 1000 USD and accept `models: []`: the refusal is ours, before the call."""
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{LITELLM}/key/generate")
        with pytest.raises(ConfigurationError):
            await _delegated().mint_key({"run_id": "r"}, budget, 60, models)
        assert not route.called


async def test_a_delegated_gateway_never_calls_the_team_routes() -> None:
    with respx.mock(assert_all_called=False) as mock:
        new, update = mock.post(f"{LITELLM}/team/new"), mock.post(f"{LITELLM}/team/update")
        gateway = _delegated()
        with pytest.raises(ConfigurationError, match="team"):
            await gateway.ensure_team("choregos-p1", 5.0)
        with pytest.raises(ConfigurationError, match="team"):
            await gateway.create_team("p1", 5.0)
        assert await ensure_project_team(gateway, "choregos-p1", 5.0) is None
        assert not new.called and not update.called


async def test_a_gateway_with_teams_still_gets_its_project_team() -> None:
    gateway = FakeGateway()
    assert await ensure_project_team(gateway, "choregos-p1", 5.0) == "choregos-p1"
    assert gateway.teams["choregos-p1"]["budget_usd"] == 5.0


@respx.mock
async def test_a_child_key_over_the_account_budget_surfaces_the_gateways_400() -> None:
    respx.post(f"{LITELLM}/key/generate").mock(
        return_value=httpx.Response(
            400, json={"error": "max_budget (150.0) cannot exceed the caller's own max_budget (100.0)"}
        )
    )
    with pytest.raises(UpstreamError) as refused:
        await _delegated().mint_key({"run_id": "r"}, 150.0, 60, ["m"])
    assert refused.value.status_code == 400


async def test_the_fake_in_delegated_mode_refuses_what_litellm_refuses() -> None:
    gateway = FakeGateway(delegated=True, account_budget_usd=10.0)
    assert "teams" not in gateway.capabilities
    with pytest.raises(ConfigurationError):
        await gateway.ensure_team("t", 1.0)
    with pytest.raises(UpstreamError) as with_team:
        await gateway.mint_key({"run_id": "a"}, 1.0, 60, ["m"], team_id="t")
    assert with_team.value.status_code == 403
    with pytest.raises(UpstreamError) as too_much:
        await gateway.mint_key({"run_id": "b"}, 11.0, 60, ["m"])
    assert too_much.value.status_code == 400
    key = await gateway.mint_key({"run_id": "c"}, 10.0, 60, ["m"])
    assert key.budget_usd == 10.0 and not gateway.key_team

"""The default SuitePlayer and the judge on the platform's gateway (S63-17, ADR 0140).

With no player registered, an installed assistant's suites are played on the model gateway (here a scripted
model server) and their rubric is scored by the judge model; without a model the promotion is refused with the
reason, unless the deployment opted out. Each case names the guard it proves.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from choregos_adapters.fakes.model_server import FakeModelServer
from choregos_api.config import get_settings
from choregos_api.services.agent_sessions import set_model_transport
from choregos_api.services.conversation_gate import set_conversation_source
from choregos_api.services.conversation_player import default_player, judge_model_of
from choregos_api.services.installed_suites import (
    current_player,
    installed_suite_source,
    set_suite_player,
)
from httpx import AsyncClient

from .kits.assistant_install import ORG as ORG_URL
from .kits.assistant_install import _install, _plugin, offline  # noqa: F401

ANALYST = f"{ORG_URL}/agents/investment-analyst"
QUOTE = "market_data__get_quote"
POSITIONS = "brokerage__list_positions"
CITED = "It trades at 12.34 EUR (source: market_data, 2026-11-10)."
CRITERION = "explains the figure without recommending a purchase"


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeModelServer]:
    """The deployment has a gateway key, and a scripted model server answers behind it."""
    server = FakeModelServer()
    settings = get_settings()
    monkeypatch.setattr(settings, "gateway_master_key", "sk-master")
    set_suite_player(None)
    set_model_transport(server.transport())
    yield server
    set_model_transport(None)
    set_suite_player(None)


#: Saving a version plays the suites (`on_publish: stable`), and so does reading its readiness: the script
#: holds a few plays.
PLAYS = 4


def _good_conversation(server: FakeModelServer) -> None:
    """What a good version does in the plugin's two cases (the answers are consumed in order)."""
    for _ in range(PLAYS):
        server.call(QUOTE, {}).say(CITED)
        server.call(POSITIONS, {}).call(QUOTE, {}).say("Your position is up.")


def _judge_says(server: FakeModelServer, text: str, status: int | None = None) -> None:
    for _ in range(PLAYS):
        server.say_for("conversation_judge", text, status=status)


def _verdict(score: float) -> str:
    return json.dumps({"scores": [{"criterion": CRITERION, "score": score, "reason": "judged"}]})


async def _installed(client: AsyncClient) -> None:
    set_conversation_source(installed_suite_source)
    await _plugin(client)
    installed = await _install(client)
    assert installed.status_code == 201, installed.text
    saved = await client.post(f"{ANALYST}/versions", json={"instructions": "Explain, v2."})
    assert saved.status_code == 201, saved.text


async def _check(client: AsyncClient) -> dict[str, Any] | None:
    url = f"{ANALYST}/versions/2/readiness"
    readiness = (await client.get(url, params={"channel": "stable"})).json()
    found: dict[str, Any] | None = next(
        (c for c in readiness["checks"] if c["name"] == "conversation_suite"), None
    )
    return found


def test_the_default_player_is_registered_when_none_is() -> None:
    set_suite_player(None)
    assert current_player() is default_player, "no player registered: the default one, never none"


def test_the_judge_model_is_configurable_with_a_default_from_the_model_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(update={"default_model_profiles": {"standard": "acme/std"}})
    assert judge_model_of(settings) == "acme/std"
    strong = settings.model_copy(update={"default_model_profiles": {"standard": "a", "strong": "acme/big"}})
    assert judge_model_of(strong) == "acme/big"
    chosen = strong.model_copy(update={"conversation_judge_model": "judge/own"})
    assert judge_model_of(chosen) == "judge/own"
    assert judge_model_of(get_settings().model_copy(update={"default_model_profiles": {}})) == (
        "platform/standard"
    )


async def test_a_version_the_judge_approves_reaches_production(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
    gateway: FakeModelServer,
) -> None:
    _good_conversation(gateway)
    _judge_says(gateway, _verdict(0.9))
    await _installed(client)
    check = await _check(client)
    assert check is not None and check["passed"] is True, check
    assert (await client.get(ANALYST)).json()["stable_version"] == 2
    # The judge went through the gateway with the deployment's key, on the configured model.
    (headers, body), *_ = gateway.side_requests
    assert headers["authorization"] == "Bearer sk-master"
    assert body["model"] == judge_model_of(get_settings())
    assert "<transcript>" in body["messages"][1]["content"]
    # The cases were played on the version's own instructions.
    assert gateway.requests[0][1]["messages"][0] == {"role": "system", "content": "Explain, v2."}


async def test_a_judge_score_below_the_threshold_refuses_production(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
    gateway: FakeModelServer,
) -> None:
    _good_conversation(gateway)
    _judge_says(gateway, _verdict(0.4))
    await _installed(client)
    check = await _check(client)
    assert check is not None and check["passed"] is False
    assert "quote-is-read-first-and-cited" in check["detail"]
    assert (await client.get(ANALYST)).json()["stable_version"] == 1, "production did not move"


@pytest.mark.parametrize("answer", ["excellent!", "", '{"scores": []}'])
async def test_a_malformed_judge_answer_refuses_production(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
    gateway: FakeModelServer,
    answer: str,
) -> None:
    _good_conversation(gateway)
    _judge_says(gateway, answer)
    await _installed(client)
    check = await _check(client)
    assert check is not None and check["passed"] is False
    assert (await client.get(ANALYST)).json()["stable_version"] == 1


async def test_a_judge_the_gateway_refuses_refuses_production(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
    gateway: FakeModelServer,
) -> None:
    _good_conversation(gateway)
    _judge_says(gateway, "budget exceeded", status=429)
    await _installed(client)
    check = await _check(client)
    assert check is not None and check["passed"] is False
    assert (await client.get(ANALYST)).json()["stable_version"] == 1


async def test_failing_assertions_skip_the_judge_on_the_gateway(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
    gateway: FakeModelServer,
) -> None:
    for _ in range(PLAYS * 2):
        gateway.call("orders__place_order", {}).say("Buy.")
    _judge_says(gateway, _verdict(1))
    await _installed(client)
    check = await _check(client)
    assert check is not None and check["passed"] is False
    assert gateway.side_requests == [], "the judge was never asked: the assertions had already failed"


async def test_a_model_refusal_while_playing_refuses_production(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
    gateway: FakeModelServer,
) -> None:
    for _ in range(PLAYS * 2):
        gateway.refuse(401, {"error": {"message": "invalid key"}})
    await _installed(client)
    check = await _check(client)
    assert check is not None and check["passed"] is False
    assert "the model could not be called" in check["detail"]


async def test_without_a_model_the_promotion_is_refused_with_the_reason(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_suite_player(None)
    monkeypatch.setattr(get_settings(), "gateway_master_key", "")
    await _installed(client)
    check = await _check(client)
    assert check is not None and check["passed"] is False
    assert "no model is configured" in check["detail"]
    refused = await client.post(f"{ANALYST}/releases", json={"channel": "stable", "version": 2})
    assert refused.status_code == 409, refused.text
    assert (await client.get(ANALYST)).json()["stable_version"] == 1, "production did not move"


async def test_the_explicit_opt_out_adds_no_check_without_a_model(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_suite_player(None)
    monkeypatch.setattr(get_settings(), "gateway_master_key", "")
    monkeypatch.setattr(get_settings(), "conversation_suites_require_model", False)
    await _installed(client)
    assert await _check(client) is None
    assert (await client.get(ANALYST)).json()["stable_version"] == 2

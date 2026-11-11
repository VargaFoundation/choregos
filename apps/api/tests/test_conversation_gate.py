"""A conversation suite counts in promotion (S63-17, ADR 0140 §2, amending ADR 0071).

The source of the suites is the installation's (S63-02): here a test source plays the investment
assistant's suite on a scripted model, so the verdict is the evaluator's own. Each case names the guard it
proves: with the check removed from `checks_for`, the version below the threshold reaches production.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_api.db.models import Agent
from choregos_api.services.conversation_gate import (
    SuiteOutcome,
    judge,
    set_conversation_source,
)
from choregos_core.conversation_evals import CaseResult, Check, SuiteResult, loop_tools, play_suite
from choregos_core.conversation_judge import judge_with
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .agent_scopes_kit import ADMIN, ORG, as_user, create_agent

pytestmark = pytest.mark.usefixtures("admin")

SUITE = Path(__file__).resolve().parents[3] / "tests/fixtures/plugins/investment-assistant/evals"
QUOTE = "market_data__get_quote"
CITED = "It trades at 12.34 EUR (source: market_data, 2026-11-10)."


def _scripted(*steps: dict[str, Any]) -> Any:
    script = list(steps)

    async def complete(body: dict[str, Any]) -> dict[str, Any]:
        return {"choices": [{"message": script.pop(0)}]}

    return complete


def _call(name: str) -> dict[str, Any]:
    call = {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps({})}}
    return {"role": "assistant", "content": None, "tool_calls": [call]}


def _say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


async def _satisfied(body: dict[str, Any]) -> dict[str, Any]:
    """A judge content with every criterion (the judge itself is proved in `test_conversation_player`)."""
    criteria = json.loads(body["messages"][1]["content"].split("<criteria>")[1].split("</criteria>")[0])
    scores = [{"criterion": c, "score": 1, "reason": "fine"} for c in criteria]
    return {"choices": [{"message": _say(json.dumps({"scores": scores}))}]}


@pytest.fixture
def conversation_model() -> Iterator[dict[int, list[dict[str, Any]]]]:
    """The scripted model of each version: what it does when the suite plays its first case."""
    scripts: dict[int, list[dict[str, Any]]] = {}

    async def source(session: AsyncSession, agent: Agent, version: int) -> list[SuiteOutcome]:
        suite = yaml.safe_load((SUITE / "investment-assistant.yaml").read_text())
        suite["cases"] = suite["cases"][:1]
        suite["threshold"] = 1.0
        result = await play_suite(
            suite,
            instructions="x",
            tools=loop_tools([QUOTE, "orders__place_order", "news__search_news"]),
            complete_for=lambda _case: _scripted(*scripts[version]),
            judge=judge_with(_satisfied, "judge"),
        )
        return [SuiteOutcome("investment-assistant", result)]

    set_conversation_source(source)
    yield scripts
    set_conversation_source(None)


async def _agent_with_two_versions(client: AsyncClient) -> None:
    await as_user(client, ADMIN)
    created = await create_agent(
        client, "analyst", spec={"instructions": "Explain."}, release_policy={"on_publish": "none"}
    )
    assert created.status_code == 201, created.text
    saved = await client.post(f"{ORG}/agents/analyst/versions", json={"instructions": "Explain, v2."})
    assert saved.status_code == 201, saved.text


def _release(client: AsyncClient) -> Any:
    return client.post(f"{ORG}/agents/analyst/releases", json={"channel": "stable", "version": 2})


async def test_a_version_below_the_suite_threshold_does_not_reach_production(
    client: AsyncClient, project: dict[str, Any], conversation_model: dict[int, list[dict[str, Any]]]
) -> None:
    # v2 reads the news first, orders, and cites nothing: it breaks three assertions.
    conversation_model[2] = [
        _call("news__search_news"),
        _call(QUOTE),
        _call("orders__place_order"),
        _say("Buy."),
    ]
    await _agent_with_two_versions(client)
    readiness = (
        await client.get(f"{ORG}/agents/analyst/versions/2/readiness", params={"channel": "stable"})
    ).json()
    check = next(c for c in readiness["checks"] if c["name"] == "conversation_suite")
    assert check["passed"] is False and "0% of cases pass, 100% required" in check["detail"]
    refused = await _release(client)
    assert refused.status_code == 409, refused.text
    assert refused.json()["errors"][0]["loc"] == ["checks", "conversation_suite"]
    agent = (await client.get(f"{ORG}/agents/analyst")).json()
    assert agent["stable_version"] == 1, "production did not move"


async def test_a_version_at_the_threshold_reaches_production(
    client: AsyncClient, project: dict[str, Any], conversation_model: dict[int, list[dict[str, Any]]]
) -> None:
    conversation_model[2] = [_call(QUOTE), _say(CITED)]
    await _agent_with_two_versions(client)
    released = await _release(client)
    assert released.status_code in (200, 201), released.text
    assert (await client.get(f"{ORG}/agents/analyst")).json()["stable_version"] == 2


async def test_an_agent_no_suite_wraps_is_not_gated(client: AsyncClient, project: dict[str, Any]) -> None:
    async def source(session: AsyncSession, agent: Agent, version: int) -> list[SuiteOutcome]:
        return []

    set_conversation_source(source)
    try:
        await _agent_with_two_versions(client)
        readiness = (
            await client.get(f"{ORG}/agents/analyst/versions/2/readiness", params={"channel": "stable"})
        ).json()
        assert all(c["name"] != "conversation_suite" for c in readiness["checks"])
        assert (await _release(client)).status_code in (200, 201)
    finally:
        set_conversation_source(None)


def test_a_suite_never_evaluated_for_the_version_fails_the_gate() -> None:
    check = judge([SuiteOutcome("s", None)])
    assert check is not None and check.passed is False and "not evaluated" in (check.detail or "")


def test_the_verdict_names_the_failing_cases_and_the_share() -> None:
    failing = CaseResult("b", [Check("must_call", False, "never called")])
    result = SuiteResult([CaseResult("a"), failing], threshold=0.75)
    check = judge([SuiteOutcome("s", result)])
    assert check is not None and not check.passed
    assert "50% of cases pass, 75% required (failing: b)" in (check.detail or "")
    assert judge([SuiteOutcome("s", SuiteResult([CaseResult("a")], threshold=1.0))]) is not None
    assert judge([]) is None

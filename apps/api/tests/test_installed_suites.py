"""An installed assistant's suites reach the promotion gate, and owners grow them (S63-17, ADR 0140).

The installation registers the suites of its plugin (`installed_suites`); a `SuitePlayer` plays them (here
on a scripted model); production is refused below a suite's threshold. A thumb down proposes a candidate;
an owner accepts it into the suites by writing its assertions, or dismisses it. Each case names the guard
it proves.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from choregos_api.db.models import AgentSessionRecord, ConversationFeedback, Organization, Project
from choregos_api.db.session import session_scope
from choregos_api.services.conversation_gate import set_conversation_source
from choregos_api.services.installed_suites import (
    installed_suite_source,
    scripted_player,
    set_suite_player,
)
from choregos_core import utcnow
from httpx import AsyncClient
from sqlalchemy import select

from .agent_scopes_kit import add_member, as_user
from .kits.assistant_install import ORG as ORG_URL
from .kits.assistant_install import _install, _plugin, offline  # noqa: F401

ANALYST = f"{ORG_URL}/agents/investment-analyst"
QUOTE = "market_data__get_quote"
CITED = "It trades at 12.34 EUR (source: market_data, 2026-11-10)."
ADMIN = "admin@varga.dev"


def _call(name: str) -> dict[str, Any]:
    call = {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps({})}}
    return {"role": "assistant", "content": None, "tool_calls": [call]}


def _say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


POSITIONS = "brokerage__list_positions"
#: What a good version does in each case of the plugin's suite (`*`: any other case, the owners' included).
GOOD = {
    "quote-is-read-first-and-cited": [_call(QUOTE), _say(CITED)],
    "positions-need-the-quote-after": [_call(POSITIONS), _call(QUOTE), _say("Your position is up.")],
    "*": [_call(QUOTE), _say(CITED)],
}
BAD = {"*": [_call("orders__place_order"), _say("Buy.")]}
#: Quotes first and cites, then recommends: the plugin's cases pass, an owner's "never say Buy." does not.
PUSHY = {**GOOD, "*": [_call(QUOTE), _say(CITED + " Buy.")]}
ACCEPT = {
    "assertions": {"must_call": [{"tool": QUOTE, "before": "any"}], "must_not_contain": ["Buy."]},
    "tool_results": {QUOTE: {"price": 12.34}},
}


@pytest.fixture
def model() -> Iterator[dict[int, dict[str, list[dict[str, Any]]]]]:
    """What the scripted model of each version does in each case (the same script, replayed per case)."""
    scripts: dict[int, dict[str, list[dict[str, Any]]]] = {}

    def complete_for(version: int, case: str) -> Any:
        by_case = scripts.get(version, GOOD)
        script = list(by_case.get(case, by_case["*"]))

        async def complete(body: dict[str, Any]) -> dict[str, Any]:
            return {"choices": [{"message": script.pop(0)}]}

        return complete

    set_suite_player(scripted_player(complete_for))
    yield scripts
    set_suite_player(None)


async def _installed_analyst(client: AsyncClient) -> None:
    set_conversation_source(installed_suite_source)  # what `create_app` does; a gate test may have removed it
    await _plugin(client)
    installed = await _install(client)
    assert installed.status_code == 201, installed.text
    found = await client.get(ANALYST)
    assert found.status_code == 200, found.text
    saved = await client.post(f"{ANALYST}/versions", json={"instructions": "Explain, v2."})
    assert saved.status_code == 201, saved.text


def _release(client: AsyncClient, version: int = 2) -> Any:
    return client.post(f"{ANALYST}/releases", json={"channel": "stable", "version": version})


async def _check(client: AsyncClient, version: int = 2) -> dict[str, Any] | None:
    url = f"{ANALYST}/versions/{version}/readiness"
    readiness = (await client.get(url, params={"channel": "stable"})).json()
    found: dict[str, Any] | None = next(
        (c for c in readiness["checks"] if c["name"] == "conversation_suite"), None
    )
    return found


async def test_the_suites_of_an_installed_assistant_stop_a_version_below_the_threshold(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
    model: dict[int, dict[str, list[dict[str, Any]]]],
) -> None:
    model[2] = BAD
    await _installed_analyst(client)
    check = await _check(client)
    assert check is not None and check["passed"] is False
    assert "investment-assistant:evals/investment-assistant.yaml" in check["detail"]
    refused = await _release(client)
    assert refused.status_code == 409, refused.text
    print("BODY", refused.text)
    assert refused.json()["errors"][0]["loc"] == ["checks", "conversation_suite"], refused.text
    # Neither route to production lets it through: saving it (`on_publish: stable`) left production at v1.
    assert (await client.get(ANALYST)).json()["stable_version"] == 1, "production did not move"


async def test_a_version_that_passes_the_installed_suites_reaches_production_on_publish(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
    model: dict[int, dict[str, list[dict[str, Any]]]],
) -> None:
    model[2] = GOOD
    await _installed_analyst(client)
    check = await _check(client)
    assert check is not None and check["passed"] is True, check
    # Saving a revision follows the agent's `on_publish` (stable by default): it moved production.
    assert (await client.get(ANALYST)).json()["stable_version"] == 2


async def test_without_a_player_an_installed_suite_adds_no_check(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
) -> None:
    """Nothing can evaluate the suites: they block nothing (a deployment registers its player)."""
    set_suite_player(None)
    await _installed_analyst(client)
    assert await _check(client) is None


async def _candidate(agent: str = "investment-analyst") -> str:
    """A thumb down's candidate, as `give_feedback` stores it, for the agent of the installed assistant."""
    async with session_scope() as db:
        org_id = (await db.execute(select(Organization.id))).scalar_one()
        project_id = (await db.execute(select(Project.id).limit(1))).scalar_one()
        record = AgentSessionRecord(
            org_id=org_id,
            project_id=project_id,
            agent_slug=agent,
            agent_version=1,
            user_id="u1",
            expires_at=utcnow() + timedelta(hours=1),
        )
        db.add(record)
        await db.flush()
        row = ConversationFeedback(
            org_id=org_id,
            session_id=record.id,
            user_id="u1",
            agent_slug=agent,
            agent_version=1,
            response_seq=1,
            rating="down",
            candidate={
                "case": {"name": "candidate-1", "turns": ["What is ACME worth?"]},
                "observed": {"tool_calls": [], "answer": "Buy."},
                "comment": None,
            },
            candidate_status="proposed",
        )
        db.add(row)
        await db.flush()
        return row.id


def _url(candidate: str, action: str) -> str:
    return f"{ANALYST}/feedback-candidates/{candidate}/{action}"


async def test_an_owner_accepts_a_candidate_into_the_suites_and_the_gate_plays_it(
    client: AsyncClient,
    org: str,
    project: dict[str, Any],
    offline: Path,  # noqa: F811
    model: dict[int, dict[str, list[dict[str, Any]]]],
) -> None:
    model[2] = GOOD
    await _installed_analyst(client)
    candidate = await _candidate()
    before = await _check(client)
    assert before is not None and before["passed"] is True
    accepted = await client.post(_url(candidate, "accept"), json=ACCEPT)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    case = accepted.json()["candidate"]["case"]
    assert case["assert"] == ACCEPT["assertions"] and case["turns"] == ["What is ACME worth?"]
    assert (await client.get(f"{ANALYST}/feedback-candidates")).json() == [], "no longer proposed"
    # The accepted case now counts: a version that quotes but ends with "Buy." passes the plugin's cases
    # and fails the owner's. Without the stored addition the gate would let it through.
    model[2] = PUSHY
    check = await _check(client)
    assert check is not None and check["passed"] is False, check
    assert case["name"] in check["detail"]
    assert (await _release(client)).status_code == 409


async def test_a_dismissed_candidate_enters_no_suite_and_a_decision_is_final(
    client: AsyncClient,
    org: str,
    project: dict[str, Any],
    offline: Path,  # noqa: F811
    model: dict[int, dict[str, list[dict[str, Any]]]],
) -> None:
    model[2] = PUSHY
    await _installed_analyst(client)
    candidate = await _candidate()
    dismissed = await client.post(_url(candidate, "dismiss"))
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"
    after = await _check(client)
    assert after is not None and after["passed"] is True
    for action in ("accept", "dismiss"):
        again = await client.post(_url(candidate, action), json=ACCEPT)
        assert again.status_code == 409, again.text
    assert (await client.post(_url("nope", "dismiss"))).status_code == 404


async def test_a_case_that_is_not_valid_for_the_agent_is_refused(
    client: AsyncClient,
    org: str,
    project: dict[str, Any],
    offline: Path,  # noqa: F811
    model: dict[int, dict[str, list[dict[str, Any]]]],
) -> None:
    await _installed_analyst(client)
    candidate = await _candidate()
    unknown_tool = {"assertions": {"must_call": [{"tool": "nothing__at_all"}]}}
    refused = await client.post(_url(candidate, "accept"), json=unknown_tool)
    assert refused.status_code == 422 and "not a `need__operation`" in refused.text
    assert (await client.post(_url(candidate, "accept"), json={"assertions": {}})).status_code == 422
    taken = {**ACCEPT, "name": "quote-is-read-first-and-cited"}
    clash = await client.post(_url(candidate, "accept"), json=taken)
    assert clash.status_code == 422 and "already in the agent's suites" in clash.text
    still = (await client.get(f"{ANALYST}/feedback-candidates")).json()
    assert [c["status"] for c in still] == ["proposed"], "a refused case decides nothing"


async def test_only_those_who_manage_the_agent_decide(
    client: AsyncClient,
    org: str,
    project: dict[str, Any],
    offline: Path,  # noqa: F811
) -> None:
    await _installed_analyst(client)
    candidate = await _candidate()
    await add_member(client, "reader@varga.dev", "viewer")
    await as_user(client, "reader@varga.dev")
    for action in ("accept", "dismiss"):
        denied = await client.post(_url(candidate, action), json=ACCEPT)
        assert denied.status_code in (403, 404), denied.text
    await as_user(client, ADMIN)
    assert (await client.get(f"{ANALYST}/feedback-candidates")).json()[0]["status"] == "proposed"

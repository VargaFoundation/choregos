"""A thumb on an answer, and the candidate case a thumb down proposes (S63-17, ADR 0140 §3).

Each case names the guard it proves. The candidate is pseudonymised before it is stored, proposed to the
agent's owners, and never added to a suite: the agent's own cases are those of its revisions only.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import load_schema
from httpx import AsyncClient
from jsonschema import Draft202012Validator

from .egress_support import as_person
from .test_agent_sessions import ORG, Upstream, _agent, _ask, upstream  # noqa: F401

CANDIDATES = f"{ORG}/agents/helper/feedback-candidates"


async def _name_the_admin(name: str) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        user.display_name = name


async def _converse(client: AsyncClient, upstream: Upstream) -> str:  # noqa: F811
    await _name_the_admin("Ada Lovelace")
    await _agent(client)
    upstream.model.say("Order 42 shipped. Ask Ada Lovelace or ada@corp.example.").say("It arrives on Friday.")
    first = await _ask(client, "I am Ada Lovelace, ada@corp.example. Where is order 42?")
    session_id = str(first.json()["session_id"])
    await _ask(client, "When does it arrive?", session_id=session_id)
    return session_id


def _feedback(session_id: str) -> str:
    return f"{ORG}/agent-sessions/{session_id}/feedback"


async def test_a_thumb_down_proposes_a_pseudonymised_candidate_to_the_owners(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    session_id = await _converse(client, upstream)
    given = await client.post(
        _feedback(session_id),
        json={"rating": "down", "comment": "Ada Lovelace says it is wrong", "response": 1},
    )
    assert given.status_code == 200, given.text
    body = given.json()
    assert (body["rating"], body["response"], body["agent_version"]) == ("down", 1, 1)
    assert body["candidate_status"] == "proposed"

    listed = await client.get(CANDIDATES)
    assert listed.status_code == 200, listed.text
    (candidate,) = listed.json()
    case = candidate["candidate"]["case"]
    assert case["turns"] == ["I am <person_1>, <email_1>. Where is order 42?"]
    assert candidate["candidate"]["observed"]["answer"] == "Order 42 shipped. Ask <person_1> or <email_1>."
    assert candidate["candidate"]["comment"] == "<person_1> says it is wrong"
    raw = listed.text
    assert "Ada" not in raw and "corp.example" not in raw and "Lovelace" not in raw, "nothing identifying"
    errors = list(
        Draft202012Validator(load_schema("conversation-evals.schema.json")).iter_errors(
            {"kind": "conversation", "agent": "helper", "cases": [case]}
        )
    )
    assert errors == [], "the candidate is a valid case of the conversation-suite format"


async def test_a_candidate_is_never_added_to_a_suite_automatically(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    session_id = await _converse(client, upstream)
    before = (await client.get(f"{ORG}/agents/helper")).json()
    await client.post(_feedback(session_id), json={"rating": "down"})
    assert len((await client.get(CANDIDATES)).json()) == 1
    after = (await client.get(f"{ORG}/agents/helper")).json()
    from choregos_api.db.models import AgentVersion
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as db:
        specs = [row.get("evals", []) for row in (await db.execute(select(AgentVersion.spec))).scalars()]
    assert specs == [[]], "no case entered the agent's own suite"
    assert after["latest_version"] == before["latest_version"], "no revision was published for it"


async def test_a_thumb_up_proposes_nothing_and_a_change_of_mind_withdraws_the_candidate(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    session_id = await _converse(client, upstream)
    up = await client.post(_feedback(session_id), json={"rating": "up", "comment": "Thanks"})
    assert up.status_code == 200 and up.json()["candidate_status"] is None
    assert (await client.get(CANDIDATES)).json() == []
    await client.post(_feedback(session_id), json={"rating": "down"})
    assert len((await client.get(CANDIDATES)).json()) == 1
    again = await client.post(_feedback(session_id), json={"rating": "up"})
    assert again.json()["id"] == up.json()["id"], "one thumb per answer, replaced"
    assert (await client.get(CANDIDATES)).json() == []


async def test_the_default_answer_is_the_latest_and_a_missing_one_is_refused(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    session_id = await _converse(client, upstream)
    latest = await client.post(_feedback(session_id), json={"rating": "down"})
    assert latest.json()["response"] == 2
    (candidate,) = (await client.get(CANDIDATES)).json()
    assert len(candidate["candidate"]["case"]["turns"]) == 2, "the conversation up to that answer"
    missing = await client.post(_feedback(session_id), json={"rating": "down", "response": 3})
    assert missing.status_code == 422, missing.text


async def test_only_the_opener_rates_and_only_those_who_manage_read_the_candidates(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    session_id = await _converse(client, upstream)
    await client.post(_feedback(session_id), json={"rating": "down"})
    async with as_person(client, "dev@varga.dev", "developer") as other:
        stranger = await other.post(_feedback(session_id), json={"rating": "down"})
        assert stranger.status_code == 404, stranger.text
        refused = await other.get(CANDIDATES)
        assert refused.status_code in (403, 404), refused.text
        assert "Ada" not in refused.text

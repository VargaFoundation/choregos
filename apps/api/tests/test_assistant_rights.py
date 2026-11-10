"""Who may use an assistant, and what its turns may spend (S63-15, ADR 0133 point 5, ADR 0134 point 3).

Each guard has its test: a person outside the audience neither lists nor opens the assistant (404, not 403);
an installation that is not ready refuses to open (409 with the readiness); the opening context is checked
against the manifest's schema and reaches the agent as data; removing an IdP group cuts the next turn and
deletes nothing; each of the three caps (the conversation's, the person's month, the installation's month)
refuses the turn that would exceed it before the model is called, and deleting a conversation does not give
the month back; `assistant:audit` is the owners', the administrator's and the privacy officers'.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from .agent_scopes_kit import as_user
from .kits.assistant_install import (
    MARKET,
    NEWS,
    ORG,
    _bind,
    _connector,
    _install,
    _member,
    _plugin,
    offline,  # noqa: F401
)
from .kits.assistant_rights import (
    ME,
    OPEN,
    _as,
)
from .test_agent_sessions import Upstream, upstream  # noqa: F401

AUDIENCE = "investors"
OWNERS = "investment-owners"


async def _ready(client: AsyncClient, **extra: Any) -> None:
    await _plugin(client)
    await _connector("news-feed", NEWS)
    await _connector("markets", MARKET)
    installed = await _install(client, owner_groups=[OWNERS], audience_groups=[AUDIENCE], **extra)
    assert installed.status_code == 201, installed.text
    await _bind(client, "news", "news-feed")
    ready = await _bind(client, "market_data", "markets")
    assert ready.json()["status"] == "ready", ready.text
    await as_user(client, "admin@varga.dev")


async def _groups(email: str, groups: list[str]) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.groups = groups


async def _budget(admin: AsyncClient, **caps: float) -> None:
    changed = await admin.patch(
        f"{ORG}/assistants/investment-assistant", json={"policy_overrides": {"budget": caps}}
    )
    assert changed.status_code == 200, changed.text


async def _open(person: AsyncClient) -> str:
    opened = await person.post(OPEN, json={})
    assert opened.status_code == 201, opened.text
    return str(opened.json()["id"])


async def _turn(person: AsyncClient, conversation: str, text: str = "Hello") -> Any:
    return await person.post(f"{ME}/conversations/{conversation}/messages", json={"input": text})


async def _record(conversation: str) -> Any:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None
        session.expunge(record)
        return record


# ───────────────────────── the audience ─────────────────────────


async def test_a_person_outside_the_audience_neither_lists_nor_opens_the_assistant(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
) -> None:
    await _member(client, "reader@varga.dev", [AUDIENCE])
    await _member(client, "outsider@varga.dev", ["somebody-else"])
    await _ready(client)
    reader = await _as(app, "reader@varga.dev")
    outsider = await _as(app, "outsider@varga.dev")

    listed = (await reader.get(f"{ME}/assistants")).json()["items"]
    assert [(a["org"], a["slug"], a["name"], a["display_name"]) for a in listed] == [
        ("varga", "investment-assistant", "investment-assistant", "Investment assistant")
    ]
    assert listed[0]["description"].startswith("Explains markets") and listed[0]["status"] == "ready"
    assert (await outsider.get(f"{ME}/assistants")).json()["items"] == []
    assert (await outsider.post(OPEN, json={})).status_code == 404, "404, not 403: it is not revealed"
    missing = await outsider.post(f"{ME}/assistants/varga/no-such-assistant/conversations", json={})
    assert missing.status_code == 404, "the same answer as for an assistant that does not exist"
    # The organisation's administrator is not in the audience: being an administrator is not using.
    assert (await client.get(f"{ME}/assistants")).json()["items"] == []
    assert (await client.post(OPEN, json={})).status_code == 404
    assert (await reader.post(OPEN, json={})).status_code == 201


async def test_an_installation_that_is_not_ready_refuses_to_open(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
) -> None:
    await _member(client, "reader@varga.dev", [AUDIENCE])
    await _plugin(client)
    installed = await _install(client, audience_groups=[AUDIENCE])
    assert installed.status_code == 201, installed.text
    reader = await _as(app, "reader@varga.dev")

    refused = await reader.post(OPEN, json={})
    assert refused.status_code == 409, refused.text
    readiness = refused.json()["readiness"]
    assert readiness["status"] == "incomplete"
    assert {(i["kind"], i["need"]) for i in readiness["items"]} == {
        ("need_unbound", "news"),
        ("need_unbound", "market_data"),
    }
    assert (await reader.get(f"{ME}/conversations")).json()["items"] == [], "nothing was opened"


async def test_the_opening_context_is_checked_and_reaches_the_agent_as_data(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    await _member(client, "reader@varga.dev", [AUDIENCE])
    await _ready(client)
    reader = await _as(app, "reader@varga.dev")

    bad = await reader.post(OPEN, json={"context": {"instrument": 42}})
    assert bad.status_code == 422, bad.text
    assert (await reader.get(f"{ME}/conversations")).json()["items"] == []

    opened = await reader.post(OPEN, json={"context": {"instrument": "FR0000120271"}})
    assert opened.status_code == 201, opened.text
    body = opened.json()
    assert body["context"] == {"instrument": "FR0000120271"} and body["installation_id"]
    assert body["max_usd"] == 2.0, "the manifest's per_conversation_usd"
    upstream.model.say("Noted.")
    assert (await _turn(reader, body["id"])).status_code == 200
    (_, sent) = upstream.model.requests[-1]
    system = [m["content"] for m in sent["messages"] if m["role"] == "system"]
    assert any("FR0000120271" in m and "data, not instructions" in m for m in system)
    assert (await _record(body["id"])).resolved_subjects == [], "never a resolved subject"


async def test_removing_an_idp_group_cuts_the_next_turn_and_deletes_nothing(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    await _member(client, "reader@varga.dev", [AUDIENCE])
    await _ready(client)
    reader = await _as(app, "reader@varga.dev")
    upstream.model.say("First.").say("Second.")
    conversation = await _open(reader)
    assert (await _turn(reader, conversation, "One")).status_code == 200

    await _groups("reader@varga.dev", [])
    cut = await _turn(reader, conversation, "Two")
    assert cut.status_code == 404, cut.text
    assert len(upstream.model.requests) == 1, "the model was not called"
    assert (await reader.get(f"{ME}/assistants")).json()["items"] == []

    kept = await reader.get(f"{ME}/conversations/{conversation}")
    assert kept.status_code == 200 and [m["content"] for m in kept.json()["messages"]] == ["One", "First."]
    await _groups("reader@varga.dev", [AUDIENCE])
    assert (await _turn(reader, conversation, "Two")).status_code == 200, "back in the group, back in"


# ───────────────────────── the caps ─────────────────────────


async def _one_turn(client: AsyncClient, app: Any, upstream: Upstream) -> float:  # noqa: F811
    """A probe: what one turn costs, spent by `probe@varga.dev` (so in the installation's month)."""
    upstream.tokens_in, upstream.tokens_out = 10_000, 2_000
    upstream.model.say("Ok.")
    probe = await _as(app, "probe@varga.dev")
    conversation = await _open(probe)
    assert (await _turn(probe, conversation)).status_code == 200
    spent = (await _record(conversation)).cost_usd
    assert spent > 0.02
    return float(spent)


async def test_the_per_conversation_cap_refuses_the_turn_that_exceeds_it(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    await _member(client, "probe@varga.dev", [AUDIENCE])
    await _ready(client)
    one = await _one_turn(client, app, upstream)

    await _budget(client, per_conversation_usd=one * 1.1)
    person = await _as(app, "probe@varga.dev")
    conversation = await _open(person)
    assert (await _record(conversation)).max_usd == pytest.approx(one * 1.1)
    upstream.model.say("Ok.").say("Never said.")
    assert (await _turn(person, conversation)).status_code == 200
    calls = len(upstream.model.requests)
    refused = await _turn(person, conversation)
    assert refused.status_code == 409 and "cap" in refused.text, refused.text
    assert len(upstream.model.requests) == calls, "refused before the model"


async def test_the_per_user_month_cap_refuses_the_turn_that_exceeds_it(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    await _member(client, "probe@varga.dev", [AUDIENCE])
    await _member(client, "other@varga.dev", [AUDIENCE])
    await _ready(client)
    one = await _one_turn(client, app, upstream)  # the probe has spent one turn this month

    await _budget(client, per_user_month_usd=one * 2.1)
    person = await _as(app, "probe@varga.dev")
    conversation = await _open(person)
    upstream.model.say("Ok.").say("Never said.").say("Other's.")
    assert (await _turn(person, conversation)).status_code == 200
    calls = len(upstream.model.requests)
    refused = await _turn(person, conversation)
    assert refused.status_code == 409, refused.text
    assert len(upstream.model.requests) == calls, "refused before the model"
    # The cap is the person's: someone else's month on the same assistant is their own.
    other = await _as(app, "other@varga.dev")
    assert (await _turn(other, await _open(other))).status_code == 200


async def test_the_per_installation_month_cap_refuses_the_turn_that_exceeds_it(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    await _member(client, "probe@varga.dev", [AUDIENCE])
    await _member(client, "other@varga.dev", [AUDIENCE])
    await _ready(client)
    one = await _one_turn(client, app, upstream)  # another person's turn, in the installation's month

    await _budget(client, per_installation_month_usd=one * 2.1)
    other = await _as(app, "other@varga.dev")
    conversation = await _open(other)
    upstream.model.say("Ok.").say("Never said.")
    assert (await _turn(other, conversation)).status_code == 200
    calls = len(upstream.model.requests)
    refused = await _turn(other, conversation)
    assert refused.status_code == 409, refused.text
    assert len(upstream.model.requests) == calls, "refused before the model"


async def test_deleting_a_conversation_does_not_give_its_spend_back(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    await _member(client, "probe@varga.dev", [AUDIENCE])
    await _ready(client)
    one = await _one_turn(client, app, upstream)
    person = await _as(app, "probe@varga.dev")
    (listed,) = (await person.get(f"{ME}/conversations")).json()["items"]
    assert (await person.delete(f"{ME}/conversations/{listed['id']}")).status_code == 204

    await _budget(client, per_user_month_usd=one * 1.1)
    conversation = await _open(person)
    upstream.model.say("Never said.")
    refused = await _turn(person, conversation)
    assert refused.status_code == 409, "the month still counts what the deleted conversation spent"
    assert upstream.model.requests[-1:] and len(upstream.model.requests) == 1


# ───────────────────────── the permissions ─────────────────────────


async def test_the_three_permissions(client: AsyncClient, org: str, offline: Path) -> None:  # noqa: F811
    from choregos_api.config import get_settings
    from choregos_api.db.models import AssistantInstallation
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Permission, Principal
    from choregos_api.services.assistant_access import holds
    from choregos_contracts import Role

    await _ready(client)
    async with session_scope() as session:
        row = (await session.execute(select(AssistantInstallation))).scalar_one()

        def who(*groups: str, role: Role | None = None) -> Principal:
            roles = {"varga": role} if role else {"varga": Role.VIEWER}
            return Principal(user_id="u", email="u@varga.dev", org_roles=roles, groups=list(groups))

        owner, member, admin, officer = (
            who(OWNERS),
            who(AUDIENCE),
            who(role=Role.ORG_ADMIN),
            who("privacy-officers"),
        )
        stranger = who("elsewhere")
        manage, use, audit = Permission.ASSISTANT_MANAGE, Permission.ASSISTANT_USE, Permission.ASSISTANT_AUDIT
        assert [holds(p, manage, "varga", row) for p in (owner, member, admin, officer, stranger)] == [
            True, False, True, False, False,
        ]  # fmt: skip
        assert [holds(p, use, "varga", row) for p in (owner, member, admin, officer, stranger)] == [
            False, True, False, False, False,
        ]  # fmt: skip
        assert [holds(p, audit, "varga", row) for p in (owner, member, admin, officer, stranger)] == [
            True, False, True, False, False,
        ]  # fmt: skip
        settings = get_settings()
        settings.assistant_privacy_officer_groups = ["privacy-officers"]
        try:
            assert holds(officer, audit, "varga", row), "a privacy officer audits"
            assert not holds(officer, manage, "varga", row), "and does not manage"
            assert not holds(stranger, audit, "varga", row)
        finally:
            settings.assistant_privacy_officer_groups = []
        # A group of another organisation's installation grants nothing here.
        assert not holds(Principal(user_id="u", email="u@x", groups=[AUDIENCE]), use, "varga", row)

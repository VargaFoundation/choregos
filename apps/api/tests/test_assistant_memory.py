"""Memory spaces of an assistant (S63-09, ADR 0136): a subject opens only once a resolver vouched for it, the
space's write rule is applied by code, a policy without memory cuts the tools, a profile conforms to its
schema, a size bound expires the oldest unpinned facts, and the person's memory page is the person's alone.

The assistant is the fixture `investment-assistant`, with two spaces added in the test's copy:
`instrument_views` (a `user_subject` space whose subject a resolver need must return, written by the agent at
once) and `house_view` (an installation space only its owners write). `instrument_notes` (resolver `none`) is
the fixture's own.
"""

# ruff: noqa: F811 - a fixture imported from another module is redefined by the parameter that names it

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from .agent_scopes_kit import as_user
from .conftest import login, without_postgres
from .kits.assistant_install import (
    FIXTURE,
)
from .kits.assistant_memory import (  # noqa: F401
    AUDIENCE,
    EXTRA,
    ISIN,
    SUBJECT,
    _agent,
    _assistant,
    _groups,
    _open,
    _stored,
    _tool,
    markets,
)
from .kits.synthetic_installation import synthetic_installation  # noqa: F401
from .test_agent_sessions import Upstream, upstream  # noqa: F401

# The installation of these tests is a row, not a plugin: its readiness is not their subject.
pytestmark = pytest.mark.usefixtures("synthetic_installation")


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Path]:
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    target = root / "investment-assistant" / "0.1.0"
    shutil.copytree(FIXTURE, target)
    manifest = target / "assistants" / "investment-assistant.yaml"
    text = manifest.read_text(encoding="utf-8")
    marker = "  - { name: preferred_sources"
    assert marker in text
    manifest.write_text(text.replace(marker, EXTRA + marker, 1), encoding="utf-8")
    use_plugin_supply(OfflineDirectory(root))
    yield root
    use_plugin_supply(None)


# ───────────────────────── the subject resolver ─────────────────────────


async def test_a_subject_no_resolver_returned_is_refused_then_opens_once_a_tool_returned_it(
    client: AsyncClient,
    admin: str,
    upstream: Upstream,
    offline: Path,
    markets: Any,
) -> None:
    await _agent(client, mcp_servers=[{"connector": "markets", "tools": ["lookup_*"]}])
    installation = await _assistant(client, with_markets=True)
    conversation = await _open(installation)

    unresolved = await _tool(
        conversation, "memory_search", space="instrument_views", query="apple", subject=SUBJECT
    )
    assert unresolved["error"] == "subject_not_resolved"
    refused = await _tool(
        conversation,
        "memory_propose",
        space="instrument_views",
        subject=SUBJECT,
        content="Apple is a long position.",
        reason="said so",
    )
    assert refused["error"] == "subject_not_resolved"
    assert await _stored("instrument_views") == [], "nothing was written, no store was created"

    # The resolver need's tool answers in the conversation, through the courtier: the subject is recorded.
    upstream.model.call("markets__lookup_instrument", {}).say("Apple, found.")
    said = await client.post(
        f"/api/v1/me/conversations/{conversation}/messages", json={"input": "Look up Apple"}
    )
    assert said.status_code == 200 and said.json()["status"] == "completed", said.text
    offered = {t["function"]["name"] for t in upstream.model.requests[0][1].get("tools", [])}
    assert {"memory_search", "memory_get_profile", "memory_propose", "markets__lookup_instrument"} <= offered

    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None
        assert record.resolved_subjects == [{"type": "instrument", "key": ISIN, "need": "market_data"}]

    opened = await _tool(
        conversation,
        "memory_propose",
        space="instrument_views",
        subject=SUBJECT,
        content="Apple is a long position.",
        reason="the person said so",
    )
    assert opened["status"] == "active", "the space's rule is `agent`"
    found = await _tool(
        conversation, "memory_search", space="instrument_views", query="long", subject=SUBJECT
    )
    assert [r["content"] for r in found["results"]] == ["Apple is a long position."]
    other = await _tool(
        conversation,
        "memory_search",
        space="instrument_views",
        query="long",
        subject={"type": "instrument", "key": "US5949181045"},
    )
    assert other["error"] == "subject_not_resolved", "only what the resolver returned opens"


async def test_a_subject_in_a_top_level_list_of_a_resolver_result_is_recorded_too(
    client: AsyncClient,
    admin: str,
    upstream: Upstream,
    offline: Path,
    markets: Any,
) -> None:
    await _agent(client, mcp_servers=[{"connector": "markets", "tools": ["get_quote"]}])
    conversation = await _open(await _assistant(client, with_markets=True))
    upstream.model.call("markets__get_quote", {}).say("Done.")
    assert (
        await client.post(f"/api/v1/me/conversations/{conversation}/messages", json={"input": "Quote"})
    ).status_code == 200
    opened = await _tool(
        conversation,
        "memory_propose",
        space="instrument_views",
        subject={"type": "instrument", "key": "FR0000120073"},
        content="Watch it.",
        reason="r",
    )
    assert opened["status"] == "active"


def test_the_subjects_of_a_structured_result() -> None:
    from choregos_api.services.assistant_memory import subjects_in

    a, b = {"type": "t", "key": "a"}, {"type": "t", "key": "b"}
    assert subjects_in({"subject": a}) == [a]
    assert subjects_in({"relations": [{"subject": a}, {"subject": b}, {"subject": a}]}) == [a, b]
    assert subjects_in([{"subject": b}]) == [b]
    assert subjects_in({"deep": {"subject": a}}) == [], "not top level, not a top-level list"
    assert subjects_in({"subject": {"type": "t"}}) == [] and subjects_in("text") == []


async def test_a_tool_of_another_need_does_not_resolve_a_subject(
    client: AsyncClient,
    admin: str,
    upstream: Upstream,
    offline: Path,
    markets: Any,
) -> None:
    """The same answer from a connector bound to no resolver need records nothing."""
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_memory import note_resolved_subjects

    installation = await _assistant(client, with_markets=True)
    conversation = await _open(installation)
    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None
        body = {"structuredContent": {"subject": SUBJECT}}
        await note_resolved_subjects(session, record, "somewhere-else", "lookup_instrument", body)
        await note_resolved_subjects(session, record, "markets", "get_history_unknown", body)
        assert record.resolved_subjects == []
        await note_resolved_subjects(session, record, "markets", "lookup_instrument", body)
        assert len(record.resolved_subjects) == 1


async def test_resolver_none_accepts_any_well_formed_subject_and_agent_proposes_writes_proposed(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    conversation = await _open(await _assistant(client))
    subject = {"type": "instrument", "key": "IE00B4L5Y983"}
    written = await _tool(
        conversation,
        "memory_propose",
        space="instrument_notes",
        subject=subject,
        content="A world ETF.",
        reason="r",
    )
    assert written["status"] == "proposed", "`agent_proposes` waits for the person"
    (fact,) = await _stored("instrument_notes")
    assert (
        fact.status == "proposed"
        and fact.subject == "IE00B4L5Y983"
        and fact.proposed_by.startswith("assistant:")
    )
    # A proposal is not remembered yet.
    found = await _tool(conversation, "memory_search", space="instrument_notes", query="ETF", subject=subject)
    assert found == {"results": []}
    for bad in (
        {"type": "instrument", "key": "has space"},
        {"type": "country", "key": "FR"},
        {"type": "instrument"},
        None,
    ):
        answer = await _tool(
            conversation, "memory_propose", space="instrument_notes", subject=bad, content="x", reason="r"
        )
        assert answer["error"] in {"subject_malformed", "subject_required"}, bad


# ───────────────────────── write rules, shapes, bounds ─────────────────────────


async def test_owner_only_and_user_only_are_never_written_active_by_a_conversation(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    conversation = await _open(await _assistant(client))
    proposal = await _tool(
        conversation, "memory_propose", space="house_view", content="Prefer ETFs.", reason="r"
    )
    assert proposal["status"] == "proposed", "an installation space is never written by a conversation"
    (fact,) = await _stored("house_view")
    assert fact.status == "proposed"
    assert (await _tool(conversation, "memory_search", space="house_view", query="ETFs"))["results"] == []
    unknown = await _tool(conversation, "memory_search", space="nope", query="x")
    assert unknown["error"] == "unknown_space"

    from choregos_api.services.assistant_memory import MemoryRefused, _status_for

    with pytest.raises(MemoryRefused) as refused:
        _status_for({"name": "mine", "writes": "user_only"})
    assert refused.value.code == "write_refused"


async def test_a_profile_patch_is_validated_against_the_schema_and_confirmed_by_the_person(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    conversation = await _open(await _assistant(client))
    wrong = await _tool(
        conversation,
        "memory_propose",
        space="investor_profile",
        profile_patch={"risk_tolerance": "reckless", "horizon_years": 5},
        reason="r",
    )
    assert wrong["error"] == "schema_violation" and "risk_tolerance" in wrong["message"]
    partial = await _tool(
        conversation,
        "memory_propose",
        space="investor_profile",
        profile_patch={"horizon_years": 5},
        reason="r",
    )
    assert partial["error"] == "schema_violation", "the merged document needs risk_tolerance"
    assert (await _tool(conversation, "memory_get_profile", space="investor_profile"))["profile"] is None

    ok = await _tool(
        conversation,
        "memory_propose",
        space="investor_profile",
        profile_patch={"risk_tolerance": "low", "horizon_years": 10},
        reason="said so",
    )
    assert ok == {"status": "proposed", "version": 1}
    assert (await _tool(conversation, "memory_get_profile", space="investor_profile"))["profile"] is None

    path = "/api/v1/me/assistants/varga/investment-assistant/memory/investor_profile"
    page = (await client.get(path)).json()
    (mine,) = page["subjects"]
    assert [(p["version"], p["status"]) for p in mine["profiles"]] == [(1, "proposed")]
    confirmed = await client.patch(path, json={"action": "confirm", "version": 1})
    assert confirmed.status_code == 200, confirmed.text
    got = await _tool(conversation, "memory_get_profile", space="investor_profile")
    assert got == {"profile": {"risk_tolerance": "low", "horizon_years": 10}, "version": 1}

    # A later patch merges into the active version; the new version supersedes it once confirmed.
    again = await _tool(
        conversation,
        "memory_propose",
        space="investor_profile",
        profile_patch={"exclusions": ["tobacco"]},
        reason="r",
    )
    assert again["version"] == 2
    await client.patch(path, json={"action": "confirm", "version": 2})
    document = (await _tool(conversation, "memory_get_profile", space="investor_profile"))["profile"]
    assert document == {"risk_tolerance": "low", "horizon_years": 10, "exclusions": ["tobacco"]}
    statuses = [
        (p["version"], p["status"]) for p in (await client.get(path)).json()["subjects"][0]["profiles"]
    ]
    assert statuses == [(2, "active"), (1, "superseded")]


async def test_max_facts_expires_the_oldest_unpinned_first(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    conversation = await _open(await _assistant(client))
    subject = {"type": "instrument", "key": "FR0000120073"}
    first = await _tool(
        conversation,
        "memory_propose",
        space="instrument_notes",
        subject=subject,
        content="fact 0",
        reason="r",
    )
    path = f"/api/v1/me/assistants/varga/investment-assistant/memory/instrument_notes/{subject['key']}"
    pinned = await client.patch(path, json={"action": "pin", "fact_id": first["id"]})
    assert pinned.status_code == 200, pinned.text
    for n in range(1, 52):  # `max_facts` of the space is 50
        await _tool(
            conversation,
            "memory_propose",
            space="instrument_notes",
            subject=subject,
            content=f"fact {n}",
            reason="r",
        )
    live = [f for f in await _stored("instrument_notes") if f.status in ("active", "proposed")]
    assert len(live) == 50
    contents = {f.content for f in live}
    assert "fact 0" in contents, "a pinned fact is kept"
    assert "fact 1" not in contents and "fact 2" not in contents, "the oldest unpinned expired"
    assert "fact 51" in contents


async def test_retention_days_expires_old_facts(client: AsyncClient, admin: str, offline: Path) -> None:
    from datetime import timedelta

    from choregos_api.db.models import MemoryFact, MemoryStore
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_memory import facts_of
    from choregos_core import utcnow

    conversation = await _open(await _assistant(client))
    subject = {"type": "instrument", "key": "FR0000120073"}
    await _tool(
        conversation, "memory_propose", space="instrument_notes", subject=subject, content="old", reason="r"
    )
    async with session_scope(orgs="*") as session:
        store = (
            await session.execute(select(MemoryStore).where(MemoryStore.slug == "instrument_notes"))
        ).scalar_one()
        store.retention_days = 30
        for fact in (await session.execute(select(MemoryFact))).scalars():
            fact.created_at = utcnow() - timedelta(days=31)
            fact.status = "active"
        await session.flush()
        assert await facts_of(session, store) == []


# ───────────────────────── the policy, the namespace, the injection ─────────────────────────


async def test_a_policy_without_memory_cuts_every_tool_and_the_injection(
    client: AsyncClient,
    admin: str,
    upstream: Upstream,
    offline: Path,
) -> None:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_memory import injection_text

    await _agent(client)
    conversation = await _open(await _assistant(client))
    written = await _tool(
        conversation,
        "memory_propose",
        space="investor_profile",
        profile_patch={"risk_tolerance": "low", "horizon_years": 3},
        reason="r",
    )
    assert written["status"] == "proposed"
    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None
        project_id = record.project_id

    upstream.model.say("Hello.")
    assert (
        await client.post(f"/api/v1/me/conversations/{conversation}/messages", json={"input": "Hi"})
    ).status_code == 200
    names = {t["function"]["name"] for t in upstream.model.requests[0][1].get("tools", [])}
    assert {"memory_search", "memory_propose", "memory_get_profile"} <= names

    policy = (
        "apiVersion: choregos/v1\nkind: Policy\nmetadata: { name: no-memory, version: 2 }\n"
        "memory: { enabled: false }\n"
    )
    put = await client.put(f"/api/v1/projects/{project_id}/policy", json={"yaml": policy})
    assert put.status_code == 200, put.text

    for name, args in (
        ("memory_search", {"space": "preferred_sources", "query": "x"}),
        ("memory_get_profile", {"space": "investor_profile"}),
        ("memory_propose", {"space": "preferred_sources", "content": "x", "reason": "r"}),
    ):
        assert (await _tool(conversation, name, **args))["error"] == "memory_disabled", name
    upstream.model.say("Hello again.")
    assert (
        await client.post(f"/api/v1/me/conversations/{conversation}/messages", json={"input": "Hi"})
    ).status_code == 200
    names = {t["function"]["name"] for t in upstream.model.requests[-1][1].get("tools", [])}
    assert not names & {"memory_search", "memory_propose", "memory_get_profile"}
    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None
        assert await injection_text(session, record) is None


async def test_the_start_of_a_conversation_carries_the_profile_and_facts_framed_as_data(
    client: AsyncClient,
    admin: str,
    upstream: Upstream,
    offline: Path,
) -> None:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_memory import injection_text

    await _agent(client)
    conversation = await _open(await _assistant(client))
    await _tool(
        conversation,
        "memory_propose",
        space="investor_profile",
        profile_patch={"risk_tolerance": "high", "horizon_years": 20},
        reason="r",
    )
    await _tool(
        conversation, "memory_propose", space="preferred_sources", content="Prefers Reuters.", reason="r"
    )
    path = "/api/v1/me/assistants/varga/investment-assistant/memory"
    await client.patch(f"{path}/investor_profile", json={"action": "confirm", "version": 1})
    facts = (await client.get(f"{path}/preferred_sources")).json()["subjects"][0]["facts"]
    await client.patch(f"{path}/preferred_sources", json={"action": "confirm", "fact_id": facts[0]["id"]})

    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None
        text = await injection_text(session, record)
    assert text is not None
    assert "not instructions" in text and '"risk_tolerance": "high"' in text and "Prefers Reuters." in text
    assert '<memory space="investor_profile"' in text

    upstream.model.say("Hi.")
    assert (
        await client.post(f"/api/v1/me/conversations/{conversation}/messages", json={"input": "Hi"})
    ).status_code == 200
    system = [m["content"] for m in upstream.model.requests[0][1]["messages"] if m["role"] == "system"]
    assert any("Prefers Reuters." in s for s in system)

    # The budget cuts: a space whose `inject_tokens` is 1 injects nothing of its facts.
    from choregos_api.services import assistant_memory

    original = assistant_memory._tokens
    assistant_memory._tokens = lambda _text: 10_000  # type: ignore[assignment]
    try:
        async with session_scope(orgs="*") as session:
            record = await session.get(AgentSessionRecord, conversation)
            assert record is not None
            assert await injection_text(session, record) is None
    finally:
        assistant_memory._tokens = original  # type: ignore[assignment]


def test_an_assistant_namespace_is_refused_by_the_run_namespace_parsers() -> None:
    from choregos_core.memory_scope import assistant_space_namespace, parse_namespace, split_namespace

    namespace = assistant_space_namespace("varga", "inst1", "user1", ("instrument", "US0378331005"))
    assert namespace == "org:varga#assistant:inst1#user:user1#subject:instrument:US0378331005"
    for form in (namespace, assistant_space_namespace("varga", "inst1"), "p#assistant:x"):
        with pytest.raises(ValueError):
            split_namespace(form)
        with pytest.raises(ValueError):
            parse_namespace(form)
    with pytest.raises(ValueError):
        assistant_space_namespace("varga", "inst1", None, ("instrument", "k"))


# ───────────────────────── the person's memory page ─────────────────────────


async def test_the_memory_page_lists_confirms_corrects_and_deletes_for_its_owner_only(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    conversation = await _open(await _assistant(client))
    subject = {"type": "instrument", "key": "FR0000120073"}
    base = "/api/v1/me/assistants/varga/investment-assistant/memory/instrument_notes"
    proposed = await _tool(
        conversation,
        "memory_propose",
        space="instrument_notes",
        subject=subject,
        content="Likes it.",
        reason="r",
    )
    listed = (await client.get(base)).json()
    assert listed["scope"] == "user_subject" and listed["writes"] == "agent_proposes"
    (entry,) = listed["subjects"]
    assert entry["subject"] == subject and [f["status"] for f in entry["facts"]] == ["proposed"]

    rejected_twice = await client.patch(
        f"{base}/{subject['key']}", json={"action": "confirm", "fact_id": proposed["id"]}
    )
    assert rejected_twice.status_code == 200
    assert rejected_twice.json()["subjects"][0]["facts"][0]["status"] == "active"
    again = await client.patch(
        f"{base}/{subject['key']}", json={"action": "confirm", "fact_id": proposed["id"]}
    )
    assert again.status_code == 409, "only a proposal is confirmed"
    corrected = await client.patch(
        f"{base}/{subject['key']}",
        json={"action": "correct", "fact_id": proposed["id"], "content": "Loves it."},
    )
    assert corrected.json()["subjects"][0]["facts"][0]["content"] == "Loves it."
    assert (await client.get(f"{base}/OTHER")).status_code == 404

    # Another person of the organisation has a page of their own: it holds nothing of this one's.
    await as_user(client, "colleague@varga.dev")
    assert (await client.get(base)).json()["subjects"] == []
    assert (await client.delete(f"{base}/{subject['key']}")).status_code == 404
    assert (await client.patch(base, json={"action": "pin", "fact_id": proposed["id"]})).status_code == 404

    await as_user(client, "admin@varga.dev")
    gone = await client.delete(f"{base}/{subject['key']}", params={"fact_id": proposed["id"]})
    assert gone.status_code == 204
    assert (await client.get(f"{base}/{subject['key']}")).json()["subjects"][0]["facts"] == []
    assert (await client.delete(f"{base}/{subject['key']}")).status_code == 204
    assert (await client.get(base)).json()["subjects"] == []
    assert (
        await client.get("/api/v1/me/assistants/varga/investment-assistant/memory/house_view")
    ).status_code == 404, "an installation space is not on a person's page"


# ───────────────────────── PostgreSQL ─────────────────────────


@without_postgres
async def test_another_person_never_reads_someones_user_space_on_postgresql(pg_app: Any) -> None:
    """Straight through RLS, whatever the router does: an administrator of the organisation sees neither the
    stores nor their facts and profiles; their owner and the platform scope do."""
    from choregos_api.db.models import MemoryFact, MemoryProfile, MemoryStore, Organization, User
    from choregos_api.db.session import limit_to_organisations, name_the_user, session_scope
    from httpx import ASGITransport

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        async with session_scope(orgs="*") as session:
            session.add(Organization(slug="a", name="A"))
        await login(client, "dev@a.test")
        await login(client, "admin@a.test")
        async with session_scope(orgs="*") as session:
            organisation = (await session.execute(select(Organization))).scalar_one()
            users = {u.email: u.id for u in (await session.execute(select(User))).scalars()}
            from choregos_api.db.models import AssistantInstallation

            installation = AssistantInstallation(
                org_id=organisation.id,
                slug="inv",
                plugin="p",
                plugin_version="1",
                manifest_digest="d",
                assistant_name="inv",
            )
            session.add(installation)
            await session.flush()
            store = MemoryStore(
                org_id=organisation.id,
                slug="notes",
                display_name="notes",
                scope="user",
                owner_key="k1",
                installation_id=installation.id,
                user_id=users["dev@a.test"],
                kind="facts",
            )
            shared = MemoryStore(
                org_id=organisation.id,
                slug="house",
                display_name="house",
                scope="installation",
                owner_key="k2",
                installation_id=installation.id,
                kind="facts",
            )
            session.add_all([store, shared])
            await session.flush()
            session.add_all(
                [
                    MemoryFact(
                        org_id=organisation.id,
                        memory_store_id=store.id,
                        kind="note",
                        subject="s",
                        content="private",
                    ),
                    MemoryFact(
                        org_id=organisation.id,
                        memory_store_id=shared.id,
                        kind="note",
                        subject="s",
                        content="house",
                    ),
                    MemoryProfile(
                        org_id=organisation.id, memory_store_id=store.id, version=1, document={"a": 1}
                    ),
                ]
            )
            store_id = store.id
        async with session_scope(orgs=["a"]) as session:
            await name_the_user(session, users["admin@a.test"])
            assert await session.get(MemoryStore, store_id) is None
            assert [m.slug for m in (await session.execute(select(MemoryStore))).scalars()] == ["house"]
            assert [f.content for f in (await session.execute(select(MemoryFact))).scalars()] == ["house"]
            assert (await session.execute(select(MemoryProfile.id))).all() == []
        async with session_scope(orgs=["a"]) as session:
            await name_the_user(session, users["dev@a.test"])
            assert await session.get(MemoryStore, store_id) is not None
            assert len((await session.execute(select(MemoryFact.id))).all()) == 2
            assert len((await session.execute(select(MemoryProfile.id))).all()) == 1
        async with session_scope() as session:  # no user named: nobody's personal memory
            await limit_to_organisations(session, ["a"])
            assert [m.slug for m in (await session.execute(select(MemoryStore))).scalars()] == ["house"]
        async with session_scope(orgs="*") as session:
            assert len((await session.execute(select(MemoryStore.id))).all()) == 2

"""Memory spaces, the gaps S63-09 left (ADR 0136): the installation owners decide what a conversation proposed
in a shared space, the person writes a `user_only` space from the page and exports everything, the injected
facts are ranked, a profile store follows the manifest's schema after an upgrade, and a subject handed in
the opening context stays a hint that opens nothing."""

# ruff: noqa: F811 - a fixture imported from a kit is redefined by the parameter that names it

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from .agent_scopes_kit import as_user
from .kits.assistant_install import FIXTURE
from .kits.assistant_memory import (  # noqa: F401
    EXTRA,
    ISIN,
    SUBJECT,
    _agent,
    _assistant,
    _open,
    _stored,
    _tool,
    markets,
)

ME = "/api/v1/me/assistants/varga/investment-assistant"
OWNERS_ROUTE = "/api/v1/orgs/varga/assistants/investment-assistant/memory"
RULES = "  - { name: my_rules, scope: user, kind: facts, writes: user_only }\n"
RISK = "risk_tolerance: { enum: [low, medium, high] }"


def _publish(root: Path, version: str, *, risk: str = RISK) -> None:
    target = root / "investment-assistant" / version
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(FIXTURE, target)
    plugin = target / "plugin.yaml"
    plugin.write_text(plugin.read_text().replace("version: 0.1.0", f"version: {version}"))
    manifest = target / "assistants" / "investment-assistant.yaml"
    text = manifest.read_text(encoding="utf-8")
    marker = "  - { name: preferred_sources"
    assert marker in text and RISK in text
    manifest.write_text(text.replace(marker, EXTRA + RULES + marker, 1).replace(RISK, risk), encoding="utf-8")


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Path]:
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    _publish(root, "0.1.0")
    use_plugin_supply(OfflineDirectory(root))
    yield root
    use_plugin_supply(None)


# ───────────────────────── the installation owners ─────────────────────────


async def test_the_installation_owners_confirm_or_reject_what_a_conversation_proposed(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    conversation = await _open(await _assistant(client))
    first = await _tool(
        conversation, "memory_propose", space="house_view", content="Prefer ETFs.", reason="r"
    )
    second = await _tool(
        conversation, "memory_propose", space="house_view", content="Avoid crypto.", reason="r"
    )
    assert first["status"] == second["status"] == "proposed"
    assert (await _tool(conversation, "memory_search", space="house_view", query="ETFs"))["results"] == []

    listed = await client.get(f"{OWNERS_ROUTE}/house_view")
    assert listed.status_code == 200, listed.text
    (entry,) = listed.json()["subjects"]
    assert listed.json()["scope"] == "installation"
    assert sorted(f["status"] for f in entry["facts"]) == ["proposed", "proposed"]

    confirmed = await client.patch(
        f"{OWNERS_ROUTE}/house_view", json={"action": "confirm", "fact_id": first["id"]}
    )
    assert confirmed.status_code == 200, confirmed.text
    rejected = await client.patch(
        f"{OWNERS_ROUTE}/house_view", json={"action": "reject", "fact_id": second["id"]}
    )
    assert rejected.status_code == 200, rejected.text
    found = await _tool(conversation, "memory_search", space="house_view", query="ETFs")
    assert [r["content"] for r in found["results"]] == ["Prefer ETFs."], "what the owners confirmed is read"
    again = await client.patch(
        f"{OWNERS_ROUTE}/house_view", json={"action": "confirm", "fact_id": first["id"]}
    )
    assert again.status_code == 409
    assert (
        await client.patch(f"{OWNERS_ROUTE}/house_view", json={"action": "pin", "fact_id": first["id"]})
    ).status_code == 422, "pinning and correcting are a person's own gestures"
    assert (await client.get(f"{OWNERS_ROUTE}/investor_profile")).status_code == 404, "a person's space"
    written = await client.patch(
        f"{OWNERS_ROUTE}/house_view", json={"action": "add", "content": "Hold cash."}
    )
    assert [f["status"] for f in written.json()["subjects"][0]["facts"] if f["content"] == "Hold cash."] == [
        "active"
    ]

    await as_user(client, "colleague@varga.dev")
    assert (await client.get(f"{OWNERS_ROUTE}/house_view")).status_code == 403
    assert (
        await client.patch(f"{OWNERS_ROUTE}/house_view", json={"action": "confirm", "fact_id": second["id"]})
    ).status_code == 403


# ───────────────────────── a person writes, and exports ─────────────────────────


async def test_a_user_only_space_is_written_from_the_page_and_the_person_exports_everything(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    conversation = await _open(await _assistant(client))
    refused = await _tool(conversation, "memory_propose", space="my_rules", content="No tobacco.", reason="r")
    assert refused["error"] == "write_refused"

    added = await client.patch(f"{ME}/memory/my_rules", json={"action": "add", "content": "No tobacco."})
    assert added.status_code == 200, added.text
    (entry,) = added.json()["subjects"]
    assert [(f["content"], f["status"]) for f in entry["facts"]] == [("No tobacco.", "active")]
    found = await _tool(conversation, "memory_search", space="my_rules", query="tobacco")
    assert [r["content"] for r in found["results"]] == ["No tobacco."], "the assistant reads what was written"
    assert (await client.patch(f"{ME}/memory/my_rules", json={"action": "add"})).status_code == 422

    subject = await client.patch(
        f"{ME}/memory/instrument_notes/{ISIN}", json={"action": "add", "content": "Bought in 2019."}
    )
    assert subject.status_code == 200, subject.text
    assert subject.json()["subjects"][0]["subject"] == SUBJECT
    assert (
        await client.patch(f"{ME}/memory/instrument_notes/bad%20key", json={"action": "add", "content": "x"})
    ).status_code == 422
    assert (
        await client.patch(f"{ME}/memory/my_rules/{ISIN}", json={"action": "add", "content": "x"})
    ).status_code == 422, "a space that is not about a subject"
    profile = await client.patch(
        f"{ME}/memory/investor_profile",
        json={"action": "add", "document": {"risk_tolerance": "low", "horizon_years": 3}},
    )
    assert profile.status_code == 200 and profile.json()["subjects"][0]["profiles"][0]["status"] == "active"

    exported = await client.get(f"{ME}/memory-export")
    assert exported.status_code == 200, exported.text
    body = exported.json()
    assert body["installation"] == "investment-assistant" and body["organisation"] == "varga"
    spaces = {s["space"]: s for s in body["spaces"]}
    assert "house_view" not in spaces, "the export is the person's memory, not the installation's"
    assert spaces["my_rules"]["subjects"][0]["facts"][0]["content"] == "No tobacco."
    assert spaces["instrument_notes"]["subjects"][0]["facts"][0]["content"] == "Bought in 2019."
    assert spaces["investor_profile"]["subjects"][0]["profiles"][0]["document"]["horizon_years"] == 3

    await as_user(client, "colleague@varga.dev")
    theirs = {s["space"]: s["subjects"] for s in (await client.get(f"{ME}/memory-export")).json()["spaces"]}
    assert all(subjects == [] for subjects in theirs.values()), "another person's export holds none of it"


# ───────────────────────── ranking ─────────────────────────


def test_facts_are_ranked_by_query_overlap_then_pinned_then_recency() -> None:
    from choregos_api.db.models import MemoryFact
    from choregos_api.services.assistant_memory import rank_facts
    from choregos_core import utcnow

    now = utcnow()

    def fact(id_: str, content: str, age_days: int, pinned: bool = False) -> MemoryFact:
        return MemoryFact(
            id=id_,
            content=content,
            created_at=now - timedelta(days=age_days),
            provenance={"pinned": pinned},
        )

    facts = [
        fact("a-new", "Likes tennis.", 1),
        fact("b-old-relevant", "Reads Reuters every morning.", 90),
        fact("c-pinned", "Allergic to peanuts.", 60, pinned=True),
        fact("d-newer", "Drives a blue car.", 2),
    ]
    ranked = [f.id for f in rank_facts(facts, "reuters")]
    assert ranked[:2] == ["c-pinned", "b-old-relevant"], "pinned first, then what the query names"
    assert ranked[2:] == ["a-new", "d-newer"], "then the newest"
    assert [f.id for f in rank_facts(facts)] == ["c-pinned", "a-new", "d-newer", "b-old-relevant"]
    assert [f.id for f in rank_facts(facts, "reuters")] == ranked, "deterministic"


async def test_the_injected_facts_are_the_relevant_ones_not_only_the_newest(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_memory import INJECTED_FACTS, injection_text

    conversation = await _open(await _assistant(client))
    await client.patch(f"{ME}/memory/my_rules", json={"action": "add", "content": "Follow Reuters closely."})
    for n in range(INJECTED_FACTS + 2):
        added = await client.patch(f"{ME}/memory/my_rules", json={"action": "add", "content": f"Filler {n}."})
        assert added.status_code == 200
    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None
        before = await injection_text(session, record)
        assert before is not None and "Reuters" not in before, "the newest five fill the budget"
        record.context = {"topic": "reuters headlines"}
        after = await injection_text(session, record)
    assert after is not None and "Follow Reuters closely." in after


# ───────────────────────── the manifest, read again ─────────────────────────


async def test_a_profile_store_follows_the_schema_of_the_upgraded_manifest_and_keeps_its_versions(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    from choregos_api.db.models import MemoryStore
    from choregos_api.db.session import session_scope

    await _assistant(client)
    base = {"risk_tolerance": "low", "horizon_years": 3}
    first = await client.patch(f"{ME}/memory/investor_profile", json={"action": "add", "document": base})
    assert first.status_code == 200, first.text
    extreme = {"risk_tolerance": "extreme", "horizon_years": 3}
    refused = await client.patch(
        f"{ME}/memory/investor_profile", json={"action": "correct", "document": extreme}
    )
    assert refused.status_code == 422, "`extreme` is not in the schema the store was created with"

    _publish(offline, "0.2.0", risk="risk_tolerance: { enum: [low, medium, high, extreme] }")
    upgraded = await client.post(
        "/api/v1/orgs/varga/plugins/investment-assistant/upgrade", json={"version": "0.2.0"}
    )
    assert upgraded.status_code == 200, upgraded.text

    accepted = await client.patch(
        f"{ME}/memory/investor_profile", json={"action": "correct", "document": extreme}
    )
    assert accepted.status_code == 200, accepted.text
    versions = accepted.json()["subjects"][0]["profiles"]
    assert [(v["version"], v["status"], v["document"]["risk_tolerance"]) for v in versions] == [
        (2, "active", "extreme"),
        (1, "superseded", "low"),
    ], "the versions written under the old schema are kept"
    async with session_scope(orgs="*") as session:
        store = (
            await session.execute(select(MemoryStore).where(MemoryStore.slug == "investor_profile"))
        ).scalar_one()
        assert "extreme" in store.schema["properties"]["risk_tolerance"]["enum"]


# ───────────────────────── a hint, never a subject ─────────────────────────


async def test_a_subject_handed_in_the_opening_context_is_a_hint_that_opens_no_space(
    client: AsyncClient, admin: str, offline: Path, markets: Any
) -> None:
    from choregos_api.db.models import AgentSessionRecord, Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Principal
    from choregos_api.services.conversations import open_conversation

    await _agent(client, mcp_servers=[{"connector": "markets", "tools": ["lookup_*"]}])
    installation = await _assistant(client, with_markets=True)
    async with session_scope(orgs="*") as session:
        organisation = (await session.execute(select(Organization))).scalar_one()
        user = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        record = await open_conversation(
            session,
            Principal(user_id=user.id, email="admin@varga.dev"),
            organisation,
            "helper",
            installation_id=installation,
            context={"subject": SUBJECT, "instrument": ISIN},
        )
        conversation = record.id
        assert not record.resolved_subjects, "the application's context resolves nothing"

    for name, args in (
        ("memory_propose", {"content": "Long position.", "reason": "r"}),
        ("memory_search", {"query": "long"}),
    ):
        refused = await _tool(conversation, name, space="instrument_views", subject=SUBJECT, **args)
        assert refused["error"] == "subject_not_resolved", name
    assert await _stored("instrument_views") == [], "no `user_subject` store was opened by the hint"
    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None and not record.resolved_subjects

"""Retention, the reads journal and erasure of an assistant's data (S63-16, ADR 0133 point 6).

Same assistant as the memory tests (`investment-assistant`, with `instrument_views` added) whose manifest also
lists `by_subject` among its erasure modes in the test's copy.
"""

# ruff: noqa: F811 - a fixture imported from another module is redefined by the parameter that names it

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from choregos_core import utcnow
from httpx import AsyncClient
from sqlalchemy import select

from .agent_scopes_kit import as_user
from .conftest import without_postgres
from .kits.assistant_install import FIXTURE, ORG
from .kits.assistant_memory import (
    EXTRA,
    ISIN,
    SUBJECT,
    _agent,
    _assistant,
    _open,
    _stored,
    _tool,
    markets,  # noqa: F401
)
from .kits.synthetic_installation import synthetic_installation  # noqa: F401
from .test_agent_sessions import Upstream, upstream  # noqa: F401

ME = "/api/v1/me/assistants/varga/investment-assistant"
BY_SUBJECT = {"subject": SUBJECT}


def _plugin_copy(tmp_path: Path, erasure: str) -> Iterator[Path]:
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    target = root / "investment-assistant" / "0.1.0"
    shutil.copytree(FIXTURE, target)
    manifest = target / "assistants" / "investment-assistant.yaml"
    text = manifest.read_text(encoding="utf-8")
    marker = "  - { name: preferred_sources"
    assert marker in text and "erasure: [by_user]" in text
    text = text.replace(marker, EXTRA + marker, 1)
    manifest.write_text(text.replace("erasure: [by_user]", f"erasure: [{erasure}]"), encoding="utf-8")
    use_plugin_supply(OfflineDirectory(root))
    yield root
    use_plugin_supply(None)


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Path]:
    """The manifest lists `by_subject` besides `by_user`."""
    yield from _plugin_copy(tmp_path, "by_user, by_subject")


@pytest.fixture
def offline_by_user_only(tmp_path: Path) -> Iterator[Path]:
    yield from _plugin_copy(tmp_path, "by_user")


async def _audience(email: str) -> None:
    """Puts the person in the installation's audience (a turn is refused outside it since S63-15)."""
    from choregos_api.db.models import AssistantInstallation, User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        for row in (await session.execute(select(AssistantInstallation))).scalars():
            row.audience_groups = ["investors"]
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.groups = ["investors"]


async def _resolved(conversation: str, *, messages: bool = False) -> None:
    """Marks the subject as resolved in the conversation, as a resolver tool would have."""
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None
        record.resolved_subjects = [{**SUBJECT, "need": "market_data"}]
        record.title = f"About {ISIN}"
        if messages:
            record.messages = [
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": f"What about {ISIN}?"},
                {"role": "assistant", "content": f"{ISIN} is Apple."},
            ]


async def _remember(conversation: str, content: str) -> None:
    written = await _tool(
        conversation,
        "memory_propose",
        space="instrument_views",
        subject=SUBJECT,
        content=content,
        reason="said so",
    )
    assert written["status"] == "active", written


async def _conversation(conversation: str) -> Any:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        if record is not None:
            session.expunge(record)
        return record


async def _events(conversation: str) -> list[Any]:
    from choregos_api.db.models import AgentSessionEvent
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return list(
            (
                await session.execute(
                    select(AgentSessionEvent).where(AgentSessionEvent.session_id == conversation)
                )
            ).scalars()
        )


# ───────────────────────── retention ─────────────────────────


async def test_the_purge_deletes_an_expired_conversation_with_its_events_and_keeps_a_fresh_one(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    from choregos_api.db.models import AgentSessionRecord, MemoryFact, MemoryStore
    from choregos_api.db.session import session_scope
    from choregos_api.services.agent_sessions import journal
    from choregos_api.services.assistant_retention import purge_once

    installation = await _assistant(client)
    old, fresh = await _open(installation), await _open(installation)
    async with session_scope(orgs="*") as session:
        for conversation, days in ((old, -1), (fresh, 20)):
            record = await session.get(AgentSessionRecord, conversation)
            assert record is not None
            record.retention_until = utcnow() + timedelta(days=days)
            await journal(session, record, "turn.ended", {"turn": 1})
    # A fact past its store's retention goes too; a pinned one stays.
    conversation = await _open(installation)
    await _resolved(conversation)
    await _remember(conversation, "Old view.")
    await _remember(conversation, "Pinned view.")
    async with session_scope(orgs="*") as session:
        store = (
            await session.execute(select(MemoryStore).where(MemoryStore.slug == "instrument_views"))
        ).scalar_one()
        store.retention_days = 10
        facts = (
            await session.execute(select(MemoryFact).where(MemoryFact.memory_store_id == store.id))
        ).scalars()
        for fact in facts:
            fact.created_at = utcnow() - timedelta(days=30)
            if fact.content == "Pinned view.":
                fact.provenance = {**(fact.provenance or {}), "pinned": True}
        await session.flush()
        done = await purge_once(session)
    assert done["conversations"] == 1 and done["memory_facts"] == 1, done
    assert await _conversation(old) is None and await _events(old) == []
    assert await _conversation(fresh) is not None and len(await _events(fresh)) >= 1
    assert [f.content for f in await _stored("instrument_views")] == ["Pinned view."]
    async with session_scope(orgs="*") as session:
        assert (await purge_once(session))["conversations"] == 0, "nothing left to purge: idempotent"


# ───────────────────────── the reads journal ─────────────────────────


@pytest.mark.usefixtures("synthetic_installation")
async def test_every_tool_call_of_a_conversation_writes_one_line_with_the_subject_and_an_auditor_reads_them(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path, markets: Any
) -> None:
    reads_url = f"{ORG}/assistants/investment-assistant/reads"
    await _agent(client, mcp_servers=[{"connector": "markets", "tools": ["lookup_*", "get_quote"]}])
    installation = await _assistant(client, with_markets=True)
    await _audience("admin@varga.dev")
    conversation = await _open(installation)
    for tool, text in (("lookup_instrument", "Apple?"), ("get_quote", "Quote?")):
        upstream.model.call(f"markets__{tool}", {}).say("Done.")
        said = await client.post(f"/api/v1/me/conversations/{conversation}/messages", json={"input": text})
        assert said.status_code == 200, said.text

    reads = await client.get(reads_url)
    assert reads.status_code == 200, reads.text
    lines = reads.json()["items"]
    assert [(r["need"], r["operation"]) for r in lines] == [
        ("market_data", "get_quote"),
        ("market_data", "lookup_instrument"),
    ], "one line per call, the latest first"
    assert all(r["conversation_id"] == conversation and r["delegation_id"] is None for r in lines)
    by_op = {r["operation"]: r for r in lines}
    assert (by_op["lookup_instrument"]["subject_type"], by_op["lookup_instrument"]["subject_key"]) == (
        "instrument",
        ISIN,
    )
    assert by_op["get_quote"]["subject_key"] == "FR0000120073", "the subject its own result carries"
    assert "Apple" not in reads.text, "never the content"

    only = await client.get(reads_url, params={"subject_key": ISIN, "user_id": lines[0]["user_id"]})
    assert [r["operation"] for r in only.json()["items"]] == ["lookup_instrument"]
    assert (await client.get(reads_url, params={"user_id": "someone-else"})).json()["items"] == []
    first = await client.get(reads_url, params={"limit": 1})
    assert first.json()["meta"]["has_more"] is True
    cursor = first.json()["meta"]["next_cursor"]
    second = await client.get(reads_url, params={"limit": 1, "cursor": cursor})
    assert [r["operation"] for r in second.json()["items"]] == ["lookup_instrument"]
    assert second.json()["meta"]["has_more"] is False

    await as_user(client, "colleague@varga.dev")
    assert (await client.get(reads_url)).status_code == 403, "a member who is not an auditor reads nothing"


# ───────────────────────── erasure by subject ─────────────────────────


async def _two_people(client: AsyncClient) -> tuple[str, str, str]:
    """An installation and, for the administrator and a colleague, a conversation where the subject was
    resolved with a memory of it; the administrator has a third conversation about nothing."""
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope

    installation = await _assistant(client)
    await as_user(client, "colleague@varga.dev")
    mine = await _open(installation, "admin@varga.dev")
    theirs = await _open(installation, "colleague@varga.dev")
    other = await _open(installation, "admin@varga.dev")
    for conversation, content in ((mine, "Admin long."), (theirs, "Colleague short.")):
        await _resolved(conversation, messages=True)
        await _remember(conversation, content)
    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, other)
        assert record is not None
        record.messages = [{"role": "user", "content": "Unrelated."}]
        record.title = "Unrelated"
    await as_user(client, "admin@varga.dev")
    return mine, theirs, other


async def test_an_export_by_subject_lists_every_users_memory_and_the_conversations_where_it_was_resolved(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    mine, theirs, other = await _two_people(client)
    exported = await client.post(
        f"{ORG}/assistants/investment-assistant/erasure", json={**BY_SUBJECT, "mode": "export"}
    )
    assert exported.status_code == 200, exported.text
    body = exported.json()
    contents = sorted(f["content"] for m in body["memories"] for f in m["facts"])
    assert contents == ["Admin long.", "Colleague short."]
    assert len({m["user_id"] for m in body["memories"]}) == 2
    assert {c["id"] for c in body["conversations"]} == {mine, theirs}
    assert other not in exported.text
    assert len(await _stored("instrument_views")) == 2, "an export removes nothing"


async def test_an_erasure_by_subject_deletes_its_memories_across_two_users_and_redacts_the_conversations(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    mine, theirs, other = await _two_people(client)
    erased = await client.post(
        f"{ORG}/assistants/investment-assistant/erasure", json={**BY_SUBJECT, "mode": "erase"}
    )
    assert erased.status_code == 200, erased.text
    assert erased.json()["memories_erased"] == 2 and erased.json()["conversations_redacted"] == 2
    assert await _stored("instrument_views") == []
    for conversation in (mine, theirs):
        record = await _conversation(conversation)
        assert record is not None, "the conversation stays: its metadata is kept"
        assert [m["content"] for m in record.messages] == ["[redacted]"] * 3
        assert record.title == "[redacted]" and record.resolved_subjects == []
        assert all(ISIN not in str(e.payload) for e in await _events(conversation))
    untouched = await _conversation(other)
    assert untouched.messages == [{"role": "user", "content": "Unrelated."}]
    assert untouched.title == "Unrelated"
    async with session_scope(orgs="*") as session:
        entries = list(
            (
                await session.execute(select(AuditLog).where(AuditLog.action == "assistant.erase.by_subject"))
            ).scalars()
        )
    assert len(entries) == 1 and entries[0].actor_id, "journaled with who did it"
    assert ISIN not in str(entries[0].payload), "the audit line holds a digest, not the key"
    assert entries[0].payload["conversations"] == 2


async def test_an_erasure_by_subject_can_delete_the_conversations_instead(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    mine, theirs, other = await _two_people(client)
    erased = await client.post(
        f"{ORG}/assistants/investment-assistant/erasure",
        json={**BY_SUBJECT, "mode": "erase", "conversations": "delete"},
    )
    assert erased.json()["conversations_deleted"] == 2
    assert await _conversation(mine) is None and await _conversation(theirs) is None
    assert await _conversation(other) is not None


async def test_a_non_auditor_is_refused_the_erasure_and_the_export(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    await _two_people(client)
    await as_user(client, "colleague@varga.dev")
    for mode in ("export", "erase"):
        refused = await client.post(
            f"{ORG}/assistants/investment-assistant/erasure", json={**BY_SUBJECT, "mode": mode}
        )
        assert refused.status_code == 403
    assert len(await _stored("instrument_views")) == 2


async def test_a_manifest_that_does_not_list_by_subject_offers_no_erasure_by_subject(
    client: AsyncClient, admin: str, upstream: Upstream, offline_by_user_only: Path
) -> None:
    await _two_people(client)
    for mode in ("export", "erase"):
        refused = await client.post(
            f"{ORG}/assistants/investment-assistant/erasure", json={**BY_SUBJECT, "mode": mode}
        )
        assert refused.status_code == 409, refused.text
    assert len(await _stored("instrument_views")) == 2


# ───────────────────────── erasure by user ─────────────────────────


async def test_an_erasure_by_user_removes_the_callers_data_and_leaves_other_peoples_intact(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    mine, theirs, other = await _two_people(client)
    gone = await client.delete(ME)
    assert gone.status_code == 200, gone.text
    assert gone.json()["conversations"] == 2 and gone.json()["memory_stores"] == 1
    assert gone.json()["memory_facts"] == 1
    assert await _conversation(mine) is None and await _conversation(other) is None
    assert await _events(mine) == []
    assert await _conversation(theirs) is not None, "the colleague's conversation is untouched"
    assert [f.content for f in await _stored("instrument_views")] == ["Colleague short."]
    async with session_scope(orgs="*") as session:
        entries = list(
            (
                await session.execute(select(AuditLog).where(AuditLog.action == "assistant.erasure.by_user"))
            ).scalars()
        )
    assert len(entries) == 1, "journaled"
    again = await client.delete(ME)
    assert again.status_code == 200 and again.json()["conversations"] == 0


# ───────────────────────── PostgreSQL ─────────────────────────


@without_postgres
async def test_the_reads_journal_stays_in_its_organisation_on_postgresql(pg_app: Any) -> None:
    from choregos_api.db.models import AssistantInstallation, AssistantRead, Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy.exc import DBAPIError

    def read(org_id: str, installation_id: str, operation: str) -> AssistantRead:
        return AssistantRead(
            org_id=org_id,
            installation_id=installation_id,
            user_id="u",
            need="n",
            operation=operation,
            at=utcnow(),
        )

    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            session.add(Organization(slug=slug, name=slug))
        await session.flush()
        ids = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        installations = {
            slug: AssistantInstallation(
                org_id=ids[slug],
                slug=f"inv-{slug}",
                plugin="p",
                plugin_version="1",
                manifest_digest="d",
                assistant_name="inv",
            )
            for slug in ("a", "b")
        }
        session.add_all(installations.values())
        await session.flush()
        for slug in ("a", "b"):
            session.add(read(ids[slug], installations[slug].id, f"op-{slug}"))
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(AssistantRead.operation))).scalars().all() == ["op-a"]
    with pytest.raises(DBAPIError):
        async with session_scope(orgs=["a"]) as session:
            session.add(read(ids["b"], installations["b"].id, "stolen"))
    async with session_scope() as session:
        assert (await session.execute(select(AssistantRead))).scalars().all() == [], "no scope, nothing"


# ───────────────────────── tasks, accounts and delegations (E3) ─────────────────────────


async def _task_artifact(email: str, installation_id: str, key: str, *, name: str = "brief") -> str:
    """A task work item in the person's personal space with one artifact; the work item's id."""
    from choregos_api.db.models import ArtifactRecord, Organization, User, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.personal_spaces import personal_space

    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        organisation = (await session.execute(select(Organization))).scalar_one()
        space = await personal_space(session, organisation, user)
        item = WorkItem(
            project_id=space.id,
            tracker_key=key,
            title=key,
            state="brief",
            documents={"task": {"installation_id": installation_id, "task": "morning_brief"}},
        )
        session.add(item)
        await session.flush()
        session.add(
            ArtifactRecord(
                org_id=organisation.id, work_item_id=item.id, name=name, version=1, type="report",
                kind="inline", media_type="text/markdown", digest="sha256:0", size=3, content="abc",
                run_id="r",
            )
        )  # fmt: skip
        return item.id


async def _artifacts() -> list[str]:
    from choregos_api.db.models import ArtifactRecord, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = await session.execute(
            select(WorkItem.tracker_key).join(ArtifactRecord, ArtifactRecord.work_item_id == WorkItem.id)
        )
        return sorted(rows.scalars())


async def test_an_erasure_by_user_reaches_accounts_delegations_subscriptions_and_task_artifacts(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path, markets: Any
) -> None:
    from choregos_api.db.models import (
        AssistantToolBinding,
        ConnectedAccount,
        Delegation,
        Organization,
        TaskSubscription,
        User,
    )
    from choregos_api.db.session import session_scope

    installation = await _assistant(client, with_markets=True)
    async with session_scope(orgs="*") as session:
        me = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        org = (await session.execute(select(Organization))).scalar_one()
        binding = (await session.execute(select(AssistantToolBinding))).scalars().all()[-1]
        binding.identity = "user_consent"  # the installation reads `markets` as the person
        colleague = User(email="colleague@varga.dev", display_name="c", oidc_sub="c")
        session.add(colleague)
        await session.flush()
        for owner in (me, colleague):
            session.add(
                ConnectedAccount(
                    org_id=org.id, user_id=owner.id, connector="markets", secret_ref=f"store:{owner.id}",
                    status="connected",
                )
            )  # fmt: skip
            session.add(
                TaskSubscription(
                    org_id=org.id, installation_id=installation, user_id=owner.id, task="morning_brief",
                    trigger={"kind": "on_demand"}, seen_keys=[],
                )
            )  # fmt: skip
            session.add(
                Delegation(
                    org_id=org.id, installation_id=installation, user_id=owner.id, task="morning_brief",
                    needs=[], granted_at=utcnow(), expires_at=utcnow() + timedelta(days=5),
                    manifest_digest="d",
                )
            )  # fmt: skip
    await _task_artifact("admin@varga.dev", installation, "TASK-mine")
    await _task_artifact("colleague@varga.dev", installation, "TASK-theirs")

    gone = await client.delete(ME)
    assert gone.status_code == 200, gone.text
    others = gone.json()["others"]
    assert others["connected_accounts"] == 1 and others["delegations"] == 1, others
    assert others["task_subscriptions"] == 1 and others["task_artifacts"] == 1, others
    assert await _artifacts() == ["TASK-theirs"], "the colleague's artifact stays"
    async with session_scope(orgs="*") as session:
        for model in (ConnectedAccount, TaskSubscription, Delegation):
            owners = (await session.execute(select(model.user_id))).scalars().all()
            assert owners == [colleague.id], model.__name__


async def test_an_erasure_by_subject_deletes_the_artifacts_of_the_task_runs_that_read_about_it(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    from choregos_api.db.models import AssistantRead, Organization, User
    from choregos_api.db.session import session_scope

    installation = await _assistant(client)
    await _task_artifact("admin@varga.dev", installation, "TASK-about", name="brief")
    await _task_artifact("admin@varga.dev", installation, "TASK-other", name="brief")
    async with session_scope(orgs="*") as session:
        me = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        org = (await session.execute(select(Organization))).scalar_one()
        for key, subject_key in (("TASK-about", SUBJECT["key"]), ("TASK-other", "ISIN-OTHER")):
            session.add(
                AssistantRead(
                    org_id=org.id, installation_id=installation, user_id=me.id, work_item_key=key,
                    need="market_data", operation="market_data__get_quote", subject_type=SUBJECT["type"],
                    subject_key=subject_key, at=utcnow(),
                )
            )  # fmt: skip
    erased = await client.post(
        f"{ORG}/assistants/investment-assistant/erasure", json={**BY_SUBJECT, "mode": "erase"}
    )
    assert erased.status_code == 200, erased.text
    assert erased.json()["task_artifacts_erased"] == 1
    assert await _artifacts() == ["TASK-other"]


async def test_the_purge_deletes_old_task_artifacts_and_old_profile_versions(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    from choregos_api.db.models import ArtifactRecord, MemoryProfile, MemoryStore, Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_retention import purge_once

    installation = await _assistant(client)
    old = await _task_artifact("admin@varga.dev", installation, "TASK-old")
    await _task_artifact("admin@varga.dev", installation, "TASK-fresh")
    async with session_scope(orgs="*") as session:
        me = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        org = (await session.execute(select(Organization))).scalar_one()
        for artifact in (
            await session.execute(select(ArtifactRecord).where(ArtifactRecord.work_item_id == old))
        ).scalars():
            artifact.created_at = utcnow() - timedelta(days=60)
        store = MemoryStore(
            org_id=org.id, slug="profile", display_name="p", scope="user", owner_key="x", kind="profile",
            installation_id=installation, user_id=me.id,
        )  # fmt: skip
        session.add(store)
        await session.flush()
        for version in range(1, 15):
            session.add(
                MemoryProfile(
                    org_id=org.id,
                    memory_store_id=store.id,
                    version=version,
                    document={"v": version},
                    status="active" if version == 14 else "superseded",
                )
            )
        # An old version that is still live (proposed, waiting for the person) is never purged.
        session.add(
            MemoryProfile(org_id=org.id, memory_store_id=store.id, version=0, document={}, status="proposed")
        )
        await session.flush()
        done = await purge_once(session)
    assert done["task_artifacts"] == 1 and done["profile_versions"] == 4, done
    assert await _artifacts() == ["TASK-fresh"]
    async with session_scope(orgs="*") as session:
        versions = sorted((await session.execute(select(MemoryProfile.version))).scalars().all())
    assert versions == [0, *range(5, 15)], "the newest ten stay, and the live ones"
    async with session_scope(orgs="*") as session:
        assert (await purge_once(session))["profile_versions"] == 0, "idempotent"

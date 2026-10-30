"""Nothing a marked work item's runs wrote leaves the platform or enters a shared memory (S42-11g,
ADR 0114 §3-6, the product owner's choices A and C).

Each test marks the work item with a restricted base and checks that the run's content (a summary, a
question, a finding's evidence, the documents) reaches neither the tracker, nor Slack, nor a pull
request's body, nor a memory; the same test without the mark proves the content would have gone.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

SECRET = "Ana earns 91k in band E4"


async def _mark(setup: Fixture, marked: bool) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.restricted_knowledge = ["hr"] if marked else None


def _tracker_text(setup: Fixture) -> str:
    items = setup.adapters.tracker.items.values()
    return "\n".join(
        [item.title for item in items]
        + [item.body or "" for item in items]
        + [comment.body for item in items for comment in item.comments]
    )


def _memory_text(setup: Fixture) -> str:
    return "\n".join(m.content for facts in setup.adapters.memory.facts.values() for m in facts)


@pytest.mark.parametrize("marked", [True, False])
async def test_the_status_comment_carries_no_run_summary(setup: Fixture, marked: bool) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.tracker import update_status_comment

    await _mark(setup, marked)
    async with session_scope() as session:
        session.add(
            Run(
                id="run-1",
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                status="succeeded",
                result={"summary": SECRET},
            )
        )
    await update_status_comment({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
    assert (SECRET in _tracker_text(setup)) is not marked


@pytest.mark.parametrize("marked", [True, False])
async def test_the_question_goes_to_the_console_not_to_the_tracker_or_slack(
    setup: Fixture, marked: bool
) -> None:
    from choregos_api.knowledge.marked import WITHHELD
    from choregos_orchestrator.activities.tracker import create_human_request

    await _mark(setup, marked)
    await create_human_request(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-1",
            "kind": "question",
            "payload": {"question": SECRET},
        }
    )
    assert (SECRET in _tracker_text(setup)) is not marked
    assert (WITHHELD in _tracker_text(setup)) is marked
    sent = setup.adapters.notify.sent[-1][1]
    assert (sent.body == WITHHELD) is marked


@pytest.mark.parametrize("marked", [True, False])
async def test_a_finding_of_a_marked_work_item_files_nothing_of_it_and_is_marked(
    setup: Fixture, marked: bool
) -> None:
    from choregos_api.db.models import Finding, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import findings as findings_activities
    from sqlalchemy import select

    await _mark(setup, marked)
    async with session_scope() as session:
        row = Finding(
            project_id=setup.project_id,
            origin_work_item_id=setup.work_item_id,
            title=f"Leak: {SECRET}",
            type="security",
            severity="critical",
            evidence=SECRET,
            estimate="S",
            status="pending",
        )
        session.add(row)
        await session.flush()
        finding_id = row.id
    outcome = await findings_activities.triage_finding(
        {"project_slug": setup.project_slug, "finding_id": finding_id}
    )
    assert outcome["status"] == "created", outcome
    assert (SECRET in _tracker_text(setup)) is not marked
    assert any(SECRET in m.body for _, m in setup.adapters.notify.sent) is not marked
    assert (SECRET in _memory_text(setup)) is not marked
    async with session_scope() as session:
        created = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == outcome["work_item_key"]))
        ).scalar_one()
        assert (created.restricted_knowledge == ["hr"]) is marked


@pytest.mark.parametrize("marked", [True, False])
async def test_a_marked_work_item_writes_no_shared_memory(setup: Fixture, marked: bool) -> None:
    from choregos_api.db.models import Finding, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from choregos_orchestrator.activities.memory import ingest_sources
    from choregos_orchestrator.activities.tracker import close_out

    await _mark(setup, marked)
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.title, item.closed_at = SECRET, utcnow()
        session.add(
            Run(
                id="run-failed",
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                status="failed",
                result={"reason": SECRET},
            )
        )
        await session.flush()
        session.add(
            Finding(
                project_id=setup.project_id,
                origin_work_item_id=setup.work_item_id,
                origin_run_id="run-failed",
                title="t",
                type="docs",
                severity="low",
                evidence=SECRET,
                status="created",
            )
        )
    await close_out({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
    summaries = [m for facts in setup.adapters.memory.facts.values() for m in facts]
    assert (not summaries) is marked, "no close-out summary of a marked work item"
    await ingest_sources(
        {"project_slug": setup.project_slug, "sources": ["closed_issues", "run_lessons", "findings"]}
    )
    assert (SECRET in _memory_text(setup)) is not marked


@pytest.mark.parametrize("marked", [True, False])
async def test_the_pull_request_body_is_the_link_and_the_code_still_ships(
    setup: Fixture, marked: bool
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.marked import WITHHELD
    from choregos_orchestrator.activities.scm import open_pull_request

    await _mark(setup, marked)
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.documents = {"spec_markdown": SECRET}
    opened = await open_pull_request({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
    assert opened["pr_url"], "the code is still delivered (choice C)"
    (pr,) = setup.adapters.scm.prs.values()
    assert (SECRET in pr.body) is not marked
    assert (WITHHELD in pr.body) is marked


async def test_a_fan_out_child_of_a_marked_parent_is_marked(setup: Fixture) -> None:
    from choregos_orchestrator.activities.children import spawn_children
    from temporalio.testing import ActivityEnvironment

    from .test_fan_out import PARENT_ID, SYMBOLS, _install, _items, _research

    await _install(setup, _research(), {"symbols": SYMBOLS})
    await _mark(setup, True)

    class Client:
        async def start_workflow(self, *_: Any, **__: Any) -> None:
            return None

    await ActivityEnvironment(client=Client()).run(  # type: ignore[arg-type]
        spawn_children,
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "workflow_id": PARENT_ID,
            "spawn": _research()["transitions"][0]["spawn"],
        },
    )
    children = [i for key, i in (await _items(setup)).items() if key != setup.tracker_key]
    assert len(children) == 2 and all(child.restricted_knowledge == ["hr"] for child in children)

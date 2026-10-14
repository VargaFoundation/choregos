"""Facts born of a run go where its agent keeps memory (S32-02).

A finding or a lesson of a run whose agent says `memory: agent` lands in the agent's own memory,
not the project's; `memory: none` writes nothing; a run without the field (older runs, playbooks)
writes to the project, as before.
"""

from __future__ import annotations

from typing import Any

from .conftest import Fixture


async def _run_with_finding(setup: Fixture, run_id: str, scope: str | None, agent: str | None) -> None:
    from choregos_api.db.models import Finding, Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        stage_input: dict[str, Any] = {} if scope is None else {"memory_scope": scope}
        session.add(
            Run(
                id=run_id,
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                status="succeeded",
                agent_slug=agent,
                stage_input=stage_input,
            )
        )
        await session.flush()
        session.add(
            Finding(
                project_id=setup.project_id,
                origin_run_id=run_id,
                title=f"proposed memory: from {run_id}",
                type="docs",
                severity="low",
                evidence=f"lesson of {run_id}",
                status="created",
            )
        )


async def test_findings_go_to_the_memory_of_their_runs_agent(setup: Fixture) -> None:
    from choregos_core import memory_namespace
    from choregos_orchestrator.activities.memory import ingest_sources

    await _run_with_finding(setup, "run-project", None, None)
    await _run_with_finding(setup, "run-agent", "agent", "coordinateur")
    await _run_with_finding(setup, "run-none", "none", "muet")
    await ingest_sources({"project_slug": setup.project_slug, "sources": ["findings"]})

    facts = setup.adapters.memory.facts

    def contents(namespace: str) -> list[str]:
        return sorted(m.content for m in facts.get(namespace, []))

    assert contents(setup.project_slug) == ["proposed memory: from run-project — lesson of run-project"]
    assert contents(memory_namespace(setup.project_slug, "agent", "coordinateur")) == [
        "proposed memory: from run-agent — lesson of run-agent"
    ]
    assert not any("run-none" in c for ns in facts for c in contents(ns)), (
        "an agent without memory writes none"
    )


async def test_a_run_lesson_follows_the_same_scope(setup: Fixture) -> None:
    from choregos_core import memory_namespace
    from choregos_orchestrator.activities.memory import write_run_lesson

    await _run_with_finding(setup, "run-agent", "agent", "coordinateur")
    await _run_with_finding(setup, "run-none", "none", "muet")
    written = await write_run_lesson(
        {"project_slug": setup.project_slug, "run_id": "run-agent", "subject": "lesson", "content": "retry"}
    )
    assert written == {"written": True}
    namespace = memory_namespace(setup.project_slug, "agent", "coordinateur")
    assert [m.content for m in setup.adapters.memory.facts[namespace]] == ["retry"]
    skipped = await write_run_lesson(
        {"project_slug": setup.project_slug, "run_id": "run-none", "subject": "lesson", "content": "x"}
    )
    assert skipped["written"] is False

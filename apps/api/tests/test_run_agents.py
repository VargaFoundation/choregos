"""S67-12: `GET /runs/{id}/agents` serves the nodes derived from the journal (ADR 0154 §5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from httpx import AsyncClient

from .test_run_transcript import _run_token

FIXTURES = Path(__file__).parents[3] / "packages" / "core" / "tests" / "fixtures" / "agent_tree"


async def _run(project: dict[str, Any], backend: str | None) -> str:
    from tranibor_api.db.models import Run, WorkItem
    from tranibor_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        item = WorkItem(project_id=project["id"], tracker_key="ACME-7", title="A fix", state="planned")
        session.add(item)
        await session.flush()
        run = Run(
            work_item_id=item.id,
            project_id=project["id"],
            stage_role="implement",
            attempt=1,
            status="running",
            backend=backend,
            stage_input={},
        )
        session.add(run)
        await session.flush()
        return str(run.id)


async def _journal(client: AsyncClient, run_id: str, fixture: str) -> None:
    events = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))["events"]
    batch = [{"seq": e["seq"], "type": e["type"], "payload": e["payload"]} for e in events]
    posted = await client.post(
        f"/api/v1/internal/runs/{run_id}/events", json={"events": batch}, headers=_run_token(run_id)
    )
    assert posted.status_code == 202, posted.text


async def test_a_claude_code_run_serves_its_sub_agents_under_the_run(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    run_id = await _run(project, "claude-code")
    await _journal(client, run_id, "claude-code-subagent.json")
    response = await client.get(f"/api/v1/runs/{run_id}/agents")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [(n["id"], n["parent_id"], n["kind"], n["tool"], n["status"], n["tool_calls"]) for n in items] == [
        (run_id, None, "agent", None, "running", 1),
        ("toolu_agent_1", run_id, "subagent", "Agent", "completed", 2),
        ("toolu_task_2", "toolu_agent_1", "subagent", "Task", "completed", 1),
        ("toolu_agent_2", run_id, "subagent", "Agent", "running", 0),
    ]
    assert items[1]["ended_at"] is not None and items[3]["ended_at"] is None
    assert all(n["started_at"] for n in items)


async def test_an_opencode_run_is_a_flat_list_with_no_sub_agent(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    run_id = await _run(project, "opencode")
    await _journal(client, run_id, "opencode-task.json")
    items = (await client.get(f"/api/v1/runs/{run_id}/agents")).json()["items"]
    assert [(n["kind"], n["tool_calls"]) for n in items] == [("agent", 3)]


async def test_the_rule_is_the_runs_backends_not_a_guess_from_the_journal(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Claude Code's journal under a run whose backend has no rule gives no sub-agent."""
    run_id = await _run(project, "codex")
    await _journal(client, run_id, "claude-code-subagent.json")
    items = (await client.get(f"/api/v1/runs/{run_id}/agents")).json()["items"]
    assert [(n["kind"], n["tool_calls"]) for n in items] == [("agent", 7)]


async def test_a_run_without_a_journal_is_its_own_agent_alone(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    run_id = await _run(project, None)
    items = (await client.get(f"/api/v1/runs/{run_id}/agents")).json()["items"]
    assert [(n["id"], n["kind"], n["tool_calls"]) for n in items] == [(run_id, "agent", 0)]


async def test_an_unknown_run_is_a_404(client: AsyncClient, project: dict[str, Any]) -> None:
    assert (await client.get("/api/v1/runs/nope/agents")).status_code == 404

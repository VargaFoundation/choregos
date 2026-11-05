"""`EvalMatrix` with its door dimension (S57-09, ADR 0122 §9): one cell per context and door, in order;
agent cases and repetitions in each cell. Its two new histories replay (`tests/replay/histories/
evals-tools-S57-09.json`, `evals-live-tools-S57-09.json`), and the histories recorded before it still do
(`tests/replay/test_replay.py`). `CHOREGOS_RECORD_HISTORY=1` records them again."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from temporalio import activity

from .conftest import recording_history

HISTORIES = Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
SCRIPTED = {
    "project_slug": "replay",
    "with_memory": [False],
    "contexts": ["none"],
    "tools": ["mcp", "scripts"],
    "agent_cases": ["netops-ssh-exposure"],
    "repetitions": 2,
}
LIVE = {
    "project_slug": "replay",
    "with_memory": [False],
    "fixtures": ["py-rounding-bug"],
    "tools": ["mcp", "scripts"],
    "agent_cases": ["netops-ssh-exposure"],
    "repetitions": 2,
    "live": True,
}


async def _play(
    payload: dict[str, Any], workflow_id: str
) -> tuple[dict[str, list[Any]], dict[str, Any], Any]:
    from choregos_orchestrator.testing import workers_by_queue
    from choregos_orchestrator.workflows.evals import EvalMatrix
    from temporalio.testing import WorkflowEnvironment

    asked: dict[str, list[Any]] = {}

    def stub(name: str, answer: Any) -> Any:
        @activity.defn(name=name)
        async def run(received: dict[str, Any]) -> Any:
            asked.setdefault(name, []).append(received)
            return answer(received) if callable(answer) else answer

        return run

    plan = {"project_id": "p", "work_item_id": "w"}
    activities = [
        stub("list_fixtures", ["py-rounding-bug"]),
        stub("run_eval_cell", lambda cell: {"tools": cell["tools"], "validated": True}),
        stub(
            "seed_eval_ticket",
            lambda p: {"skipped": bool(p.get("agent_case")), "reason": "agent", "plan": plan},
        ),
        stub("prepare_stage", {"run_id": "r1", "stage_input": {"budget": {"max_minutes": 30}}}),
        stub("start_run", None),
        stub("await_run", {"result": {"status": "done"}}),
        stub("record_run_outcome", {}),
        stub("collect_spend", {}),
        stub("judge_eval_run", lambda p: {"fixture": p["fixture"], "ok": True, "skipped": False}),
        stub("summarize_eval_cell", lambda p: {"tools": p["tools"], "outcomes": p["outcomes"]}),
        stub("publish_matrix", {}),
    ]
    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with workers_by_queue(
            environment.client, activities=activities, workflows=[EvalMatrix], workflow_queue="replay"
        ):
            handle = await environment.client.start_workflow(
                "EvalMatrix", payload, id=workflow_id, task_queue="replay"
            )
            result = await handle.result()
            history = await handle.fetch_history()
    finally:
        await environment.shutdown()
    return asked, result, history


async def _replays(history: Any, name: str | None) -> None:
    """The fresh history replays; recorded under `name` when histories are recorded (`None`: never, the
    archived one is the shape of an older code)."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history() and name is not None:
        target = HISTORIES / f"{name}.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_scripted_matrix_asks_one_cell_per_door_with_its_cases_and_repetitions() -> None:
    asked, result, history = await _play(SCRIPTED, "evals-tools-replay")
    cells = asked["run_eval_cell"]
    assert [cell["tools"] for cell in cells] == ["mcp", "scripts"]
    assert all(cell["agent_cases"] == ["netops-ssh-exposure"] and cell["repetitions"] == 2 for cell in cells)
    assert result["cells"] == 2
    await _replays(history, "evals-tools-S57-09")


async def test_a_matrix_without_doors_asks_what_it_asked_before() -> None:
    """The defaults: one `mcp` cell per context, no agent case, one repetition, no new key."""
    asked, _, _ = await _play({"project_slug": "replay", "with_memory": [True]}, "evals-defaults-replay")
    [cell] = asked["run_eval_cell"]
    assert cell["tools"] == "mcp" and "agent_cases" not in cell and "repetitions" not in cell


async def test_a_live_matrix_plays_each_ticket_per_repetition_and_seeds_the_agent_cases() -> None:
    """Since S57-11a an agent case is seeded once per repetition too (`LIVE_AGENT_CASES`); the history this
    test archived before it (`evals-live-tools-S57-09.json`, one seed per agent case) still replays, and
    is not recorded again: `test_eval_live_agent_cases_history.py` records the new shape."""
    asked, result, history = await _play(LIVE, "evals-live-tools-replay")
    seeds = asked["seed_eval_ticket"]
    assert [(s["tools"], s.get("repetition", 0), s.get("agent_case")) for s in seeds] == [
        ("mcp", 0, None),
        ("mcp", 1, None),
        ("mcp", 0, "netops-ssh-exposure"),
        ("mcp", 1, "netops-ssh-exposure"),
        ("scripts", 0, None),
        ("scripts", 1, None),
        ("scripts", 0, "netops-ssh-exposure"),
        ("scripts", 1, "netops-ssh-exposure"),
    ]
    judged = asked["judge_eval_run"]
    assert [(j["tools"], j["repetition"]) for j in judged] == [
        ("mcp", 0),
        ("mcp", 1),
        ("scripts", 0),
        ("scripts", 1),
    ]
    assert [len(cell["outcomes"]) for cell in result["results"]] == [4, 4]
    await _replays(history, None)


def test_the_new_histories_are_archived() -> None:
    for name in ("evals-tools-S57-09", "evals-live-tools-S57-09"):
        recorded = json.loads((HISTORIES / f"{name}.json").read_text(encoding="utf-8"))
        started = recorded["events"][0]["workflowExecutionStartedEventAttributes"]
        assert started["workflowType"]["name"] == "EvalMatrix"

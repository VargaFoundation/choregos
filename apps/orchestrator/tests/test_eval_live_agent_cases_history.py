"""`EvalMatrix` plays its agent cases live (S57-11a): each case once per repetition, as a stage of its own
eval project, judged as the case it is (`agent_case`). The new history replays
(`tests/replay/histories/evals-live-agent-cases-S57-11a.json`), and the one recorded before it, where each
agent case was seeded once and skipped, still does (`LIVE_AGENT_CASES`; `tests/replay/test_replay.py`
replays every archived history). `CHOREGOS_RECORD_HISTORY=1` records the new one again."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from temporalio import activity

from .conftest import recording_history

HISTORIES = Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
NEW = "evals-live-agent-cases-S57-11a"
BEFORE = "evals-live-tools-S57-09"
LIVE = {
    "project_slug": "replay",
    "with_memory": [False],
    "fixtures": ["py-rounding-bug"],
    "tools": ["mcp", "scripts"],
    "agent_cases": ["netops-ssh-exposure", "identity-offboarding"],
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

    def seeded(received: dict[str, Any]) -> dict[str, Any]:
        project = "case-project" if received.get("agent_case") else "eval-project"
        return {"skipped": False, "reason": "", "plan": {"project_id": project, "work_item_id": "w"}}

    activities = [
        stub("list_fixtures", ["py-rounding-bug"]),
        stub("seed_eval_ticket", seeded),
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


async def test_a_live_matrix_plays_each_agent_case_per_repetition_and_judges_it_as_a_case() -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    asked, result, history = await _play(LIVE, "evals-live-agent-cases-replay")
    seeds = [(s["tools"], s.get("repetition", 0), s.get("agent_case")) for s in asked["seed_eval_ticket"]]
    cases = [None, "netops-ssh-exposure", "identity-offboarding"]
    assert seeds == [(tools, rep, case) for tools in ("mcp", "scripts") for case in cases for rep in (0, 1)]
    judged = [(j["tools"], j["repetition"], j.get("agent_case")) for j in asked["judge_eval_run"]]
    assert judged == seeds, "every case is judged, each agent case as itself"
    prepared = [p["project_id"] for p in asked["prepare_stage"]]
    assert prepared.count("case-project") == 8, "an agent case runs in the project its seeding named"
    assert [len(cell["outcomes"]) for cell in result["results"]] == [6, 6]
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / f"{NEW}.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


def _seeds_of(recorded: dict[str, Any]) -> list[dict[str, Any]]:
    """The inputs of the `seed_eval_ticket` activities a history scheduled."""
    import base64

    found = []
    for event in recorded["events"]:
        attributes = event.get("activityTaskScheduledEventAttributes")
        if attributes and attributes["activityType"]["name"] == "seed_eval_ticket":
            [argument] = attributes["input"]["payloads"]
            found.append(json.loads(base64.b64decode(argument["data"])))
    return found


def test_the_new_history_is_archived_and_the_one_before_it_seeded_each_agent_case_once() -> None:
    new = json.loads((HISTORIES / f"{NEW}.json").read_text(encoding="utf-8"))
    started = new["events"][0]["workflowExecutionStartedEventAttributes"]
    assert started["workflowType"]["name"] == "EvalMatrix"
    assert sum(1 for s in _seeds_of(new) if s.get("agent_case")) == 8, (
        "two cases × two doors × two repetitions"
    )
    before = _seeds_of(json.loads((HISTORIES / f"{BEFORE}.json").read_text(encoding="utf-8")))
    assert [s.get("repetition", 0) for s in before if s.get("agent_case")] == [0, 0], (
        "the history recorded before S57-11a seeds each agent case once: the patch keeps it replaying"
    )

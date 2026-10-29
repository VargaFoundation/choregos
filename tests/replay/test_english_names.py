"""English Temporal names, French ones kept (S34-02, ADR 0046).

Eight activities and one signal were born French. They are renamed to English, but a recorded
history — and every workflow in flight at deploy time — still names the French ones: the worker
keeps answering to both, and the workflow accepts both signals. The patch ids already in
histories are NEVER renamed: a patch id that changes is a marker the replayer no longer finds.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR = ROOT / "apps" / "orchestrator" / "src"
WORKFLOWS = ORCHESTRATOR / "choregos_orchestrator" / "workflows"
HISTORIES = Path(__file__).parent / "histories"

#: English → French, the eight activities renamed by S34-02.
RENAMED = {
    "load_action": "charger_l_action",
    "execute_effect": "executer_l_effet",
    "compensate_effect": "compenser_l_effet",
    "close_action": "cloturer_l_action",
    "propose_transition_action": "proposer_l_action_de_transition",
    "mark_awaiting_evidence": "marquer_l_attente",
    "action_state": "etat_de_l_action",
    "confirm_auto_sync": "confirmer_auto_sync",
}
#: The patch ids recorded in histories before English became the rule. Frozen.
FROZEN_PATCH_IDS = (
    "preuve-attendue",
    "migration-par-definition",
    "actions-de-transition",
    "taches-humaines",
    "train-par-environnement",
    "auto-sync-previent",
)


def _source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("*.py")))


@pytest.mark.parametrize("patch_id", FROZEN_PATCH_IDS)
def test_a_french_patch_id_is_never_renamed(patch_id: str) -> None:
    source = _source()
    declared = re.search(rf'^(\w+) = "{re.escape(patch_id)}"  # lang: fr-ok', source, re.M)
    assert declared, f"patch id `{patch_id}` is gone (or lost its `lang: fr-ok`): recorded histories need it"
    assert f"workflow.patched({declared.group(1)})" in source, f"`{patch_id}` is no longer checked"


def test_the_worker_answers_to_both_names() -> None:
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.repartition import nom_de_l_activite

    names = {nom_de_l_activite(activity) for activity in ALL_ACTIVITIES}
    missing = sorted(n for pair in RENAMED.items() for n in pair if n not in names)
    assert not missing, f"not registered on the worker: {missing}"


def _is_activity_defn(node: ast.expr) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "defn"
        and ast.unparse(target.value) == "activity"
    )


def test_every_activity_names_itself() -> None:
    """An activity's wire name is the `name=` of its decorator, never its Python function name: a
    rename of the function (S45) then leaves every recorded history and every workflow in flight
    scheduling a name the worker still answers to (S45-03)."""
    seen, bare = 0, []
    for path in sorted(ORCHESTRATOR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if not _is_activity_defn(decorator):
                    continue
                seen += 1
                named = isinstance(decorator, ast.Call) and any(k.arg == "name" for k in decorator.keywords)
                if not named:
                    bare.append(f"{path.relative_to(ROOT)}:{decorator.lineno} {node.name}")
    assert seen, "no `@activity.defn` found: the scan reads nothing"
    assert not bare, f"`@activity.defn` without `name=`: {bare}"


def test_the_workflow_takes_both_signals() -> None:
    from choregos_orchestrator.workflows.action import ActionWorkflow
    from temporalio.workflow import _Definition

    signals = set(_Definition.must_from_class(ActionWorkflow).signals)
    assert {"preuve", "evidence"} <= signals


def _scheduled_and_signals(path: Path) -> tuple[set[str], set[str]]:
    events: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["events"]
    activities = {
        e["activityTaskScheduledEventAttributes"]["activityType"]["name"]
        for e in events
        if "activityTaskScheduledEventAttributes" in e
    }
    signals = {
        e["workflowExecutionSignaledEventAttributes"]["signalName"]
        for e in events
        if "workflowExecutionSignaledEventAttributes" in e
    }
    return activities, signals


def test_the_archive_holds_histories_with_both_names() -> None:
    """`test_replay.py` replays every archived history; this says the archive still proves both
    sides: French histories recorded before S34-02, English ones recorded after."""
    activities: set[str] = set()
    signals: set[str] = set()
    for path in sorted(HISTORIES.glob("*.json")):
        found, sent = _scheduled_and_signals(path)
        activities |= found
        signals |= sent
    french = {"charger_l_action", "executer_l_effet", "cloturer_l_action", "proposer_l_action_de_transition"}
    english = {
        "load_action",
        "execute_effect",
        "close_action",
        "propose_transition_action",
        "confirm_auto_sync",
    }
    assert french <= activities, sorted(french - activities)
    assert english <= activities, sorted(english - activities)
    assert {"preuve", "evidence"} <= signals

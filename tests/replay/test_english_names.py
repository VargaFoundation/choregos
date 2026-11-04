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
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.rglob("*.py")))


@pytest.mark.parametrize("patch_id", FROZEN_PATCH_IDS)
def test_a_french_patch_id_is_never_renamed(patch_id: str) -> None:
    source = _source()
    declared = re.search(rf'^(\w+) = "{re.escape(patch_id)}"  # lang: fr-ok', source, re.M)
    assert declared, f"patch id `{patch_id}` is gone (or lost its `lang: fr-ok`): recorded histories need it"
    assert f"workflow.patched({declared.group(1)})" in source, f"`{patch_id}` is no longer checked"


def test_the_worker_answers_to_both_names() -> None:
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.queues import activity_name

    names = {activity_name(activity) for activity in ALL_ACTIVITIES}
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


# ───────────────── English payload keys (S45-39, ADR 0098 §4) ─────────────────

#: The patch that makes a workflow SEND English keys, and the three histories recorded under it.
ENGLISH_PAYLOAD_PATCH = "english-payload-keys"
ENGLISH_KEY_HISTORIES = {
    "action-english-keys-S45-39": ("mark_awaiting_evidence", {"waiting", "until", "evidence"}),
    "train-english-keys-S45-39": ("confirm_auto_sync", {"conclude"}),
    "wi-migration-english-keys-S45-39": ("record_migration", {"key"}),
}
#: The histories recorded before it, which keep the former keys and are replayed as they were.
FORMER_KEY_HISTORIES = {
    "action-evidence-english-S34-02": ("mark_awaiting_evidence", {"en_attente", "jusqu_a", "preuve"}),
    "train-auto-sync-english-S34-02": ("confirm_auto_sync", {"conclure"}),
    "wi-migration-S16-05": ("record_migration", {"cle"}),
}


def _decoded(data: str) -> Any:
    import base64

    return json.loads(base64.b64decode(data))


def _patches(path: Path) -> set[str]:
    events: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["events"]
    found = set()
    for event in events:
        marker = event.get("markerRecordedEventAttributes")
        if marker and marker["markerName"] == "core_patch":
            found.add(_decoded(marker["details"]["patch-data"]["payloads"][0]["data"])["id"])
    return found


def _sent_keys(path: Path, activity: str) -> set[str]:
    """The keys of every payload the workflow sent to `activity`."""
    events: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["events"]
    keys: set[str] = set()
    for event in events:
        scheduled = event.get("activityTaskScheduledEventAttributes")
        if scheduled and scheduled["activityType"]["name"] == activity:
            for payload in scheduled["input"]["payloads"]:
                body = _decoded(payload["data"])
                keys |= set(body) if isinstance(body, dict) else set()
    return keys


def test_the_english_payload_patch_is_declared_and_used() -> None:
    source = _source()
    declared = re.search(rf'^(\w+) = "{re.escape(ENGLISH_PAYLOAD_PATCH)}"', source, re.M)
    assert declared, "the patch id is not declared in the workflows"
    assert f"workflow.patched({declared.group(1)})" in source, "the patch is never checked"
    # Used by every workflow that sends a key: the action, the train and the interpreter's migration.
    for module in ("action.py", "train.py", "interpreter_parts/external.py"):
        assert "english_payload_keys()" in (WORKFLOWS / module).read_text(encoding="utf-8"), module


@pytest.mark.parametrize("name", sorted(ENGLISH_KEY_HISTORIES))
def test_a_new_history_sends_english_keys(name: str) -> None:
    activity, english = ENGLISH_KEY_HISTORIES[name]
    path = HISTORIES / f"{name}.json"
    assert ENGLISH_PAYLOAD_PATCH in _patches(path)
    sent = _sent_keys(path, activity)
    assert english <= sent, sorted(english - sent)
    former = set().union(*(k for _, k in FORMER_KEY_HISTORIES.values()))
    assert not sent & former, sorted(sent & former)


@pytest.mark.parametrize("name", sorted(FORMER_KEY_HISTORIES))
def test_an_old_history_keeps_its_french_keys(name: str) -> None:
    """Replayed as it was (`test_replay.py`): no marker, and the keys it recorded."""
    activity, former = FORMER_KEY_HISTORIES[name]
    path = HISTORIES / f"{name}.json"
    assert ENGLISH_PAYLOAD_PATCH not in _patches(path)
    assert former <= _sent_keys(path, activity)

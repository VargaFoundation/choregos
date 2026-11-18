# SPDX-License-Identifier: Apache-2.0
"""An INSTALLED extension's step effect is what `does:` names, and the interpreter plays it
(ADR 0181, round C0).

The extension is a real module with its `.dist-info`, loaded by `load_extensions()` as at a worker's
start-up: a test plugin `itsm` registers `close_incident`, which runs once the transition's gates have
passed. A workflow naming it is published, the real interpreter crosses the transition on Temporal's
test server, and the history replays without the extension (the interpreter never reads the registry:
`run_step_effect`'s answers are in the history). It is archived for `tests/replay`
(`TRANIBOR_RECORD_HISTORY=1`).
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from .conftest import Fixture, recording_history, stage_result

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"

EXTENSION = """
from tranibor_core.step_effects import ENTERED, PASSED, step_effect

#: What the test reads and sets: the requests each effect was given, a failure to raise.
STATE = {"closed": [], "opened": [], "fail": False}


async def close_incident(request):
    if STATE["fail"]:
        raise ConnectionError("the ITSM is unreachable")
    STATE["closed"].append(request)
    return {"detail": "incident closed"}


async def open_bridge(request):
    STATE["opened"].append(request)
    return None


def plug_in():
    step_effect(
        "close_incident", gesture="closes the incident", when=PASSED, plugin="itsm", execute=close_incident
    )
    step_effect("open_bridge", gesture="opens a bridge", when=ENTERED, plugin="itsm", execute=open_bridge)
"""

DESK = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: incident-desk, version: 1 }
initial: inbox
actors:
  responder: { type: agent, role: triage, model: "profile:standard" }
  desk: { type: system }
states:
  inbox: { display: To do }
  checked: { display: Checked }
  closed: { display: Closed, terminal: true }
transitions:
  - { id: t-triage, from: inbox, to: checked, by: responder, outputs: [summary] }
  - { id: t-close, from: checked, to: closed, by: desk, does: close_incident }
"""


@pytest.fixture
def extension(tmp_path: pathlib.Path) -> Iterator[dict[str, Any]]:
    from tranibor_adapters import load_extensions
    from tranibor_core import step_effects

    root = tmp_path / "site"
    root.mkdir()
    (root / "extension_desk.py").write_text(EXTENSION, encoding="utf-8")
    info = root / "extension_desk-0.1.0.dist-info"
    info.mkdir()
    metadata = "Metadata-Version: 2.1\nName: extension_desk\nVersion: 0.1.0\n"
    (info / "METADATA").write_text(metadata, encoding="utf-8")
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\nextension_desk = extension_desk:plug_in\n", encoding="utf-8"
    )
    sys.path.insert(0, str(root))
    try:
        assert "extension_desk" in load_extensions()
        yield sys.modules["extension_desk"].STATE
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("extension_desk", None)
        step_effects.forget("itsm")


async def _start(setup: Fixture, temporal_env: Any) -> Any:
    from tranibor_api.db.models import Project, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_api.rbac import SYSTEM
    from tranibor_api.services.definitions import publish_workflow
    from tranibor_api.temporal import interpreter_id

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        definition = await publish_workflow(session, SYSTEM, project, DESK)
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id
    return await temporal_env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id=interpreter_id(setup.project_slug, setup.tracker_key),
        task_queue="test",
    )


async def test_the_interpreter_plays_the_extension_s_effect_and_the_history_replays_without_it(
    setup: Fixture, temporal_env: Any, worker_factory: Any, extension: dict[str, Any]
) -> None:
    from temporalio.worker import Replayer
    from tranibor_core import step_effects
    from tranibor_orchestrator.workflows import ALL_WORKFLOWS

    setup.adapters.executor.handler = lambda stage_input: stage_result(
        "triaged", outputs={"summary": "a disk"}
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await _start(setup, temporal_env)
            outcome = await asyncio.wait_for(handle.result(), timeout=60)
    assert outcome["state"] == "closed"
    (request,) = extension["closed"]
    assert (request.effect, request.transition, request.work_item_id) == (
        "close_incident",
        "t-close",
        setup.work_item_id,
    )
    assert request.key.endswith(":t-close:1:close_incident") and request.project is not None

    history = await handle.fetch_history()
    step_effects.forget("itsm")  # the history must not need the extension to replay
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-step-effect-S83-02c.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


def _payload(setup: Fixture, effect: str, moment: str) -> dict[str, Any]:
    return {
        "effect": effect,
        "moment": moment,
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition": "t-close",
        "attempt": 2,
        "workflow_id": "wf",
    }


async def test_each_effect_runs_at_its_own_moment(setup: Fixture, extension: dict[str, Any]) -> None:
    from tranibor_orchestrator.activities.step_effects import run_step_effect

    entered = await run_step_effect(_payload(setup, "close_incident", "entered"))
    assert entered == {"ok": True, "ran": False, "later": True, "detail": ""}
    assert extension["closed"] == []
    passed = await run_step_effect(_payload(setup, "close_incident", "passed"))
    assert passed == {"ok": True, "ran": True, "later": False, "detail": "incident closed"}
    assert extension["closed"][0].key == "wf:t-close:2:close_incident"

    bridge = await run_step_effect(_payload(setup, "open_bridge", "entered"))
    assert bridge["ran"] and not bridge["later"] and len(extension["opened"]) == 1


async def test_a_failing_or_missing_effect_refuses_the_crossing_naming_it(
    setup: Fixture, extension: dict[str, Any]
) -> None:
    from tranibor_core import step_effects
    from tranibor_orchestrator.activities.step_effects import run_step_effect

    extension["fail"] = True
    failed = await run_step_effect(_payload(setup, "close_incident", "passed"))
    assert not failed["ok"] and "`does: close_incident` of the plugin `itsm` failed" in failed["detail"]

    step_effects.forget("itsm")
    missing = await run_step_effect(_payload(setup, "close_incident", "entered"))
    assert not missing["ok"] and "is not installed in this deployment" in missing["detail"]

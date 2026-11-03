# SPDX-License-Identifier: Apache-2.0
"""S47-33: `activities/stage.py` stays small, and keeps its activities and its patched helpers.

A bare `@activity.defn` names the activity after its function, and tests monkeypatch
`stage._resolve_model`: the activities, `_resolve_model` and its caller therefore stay in `stage`,
and every helper moved out is still importable from it.
"""

from __future__ import annotations

import json
from pathlib import Path

from choregos_orchestrator import activities
from choregos_orchestrator.activities import stage

STAGE = Path(stage.__file__)
FIXTURE = Path(__file__).parent / "fixtures" / "activity_names.json"
MAX_LINES = 800
#: Names the tests monkeypatch or import from `stage`: they stay importable from it.
IMPORTED_FROM_STAGE = (
    "StagePlan",
    "MEMORY_DISABLED",
    "ResolvedMemories",
    "_journal_skipped_memories",
    "_mcp_servers",
    "_serveurs_mcp",
    "_refuse_on_budget",
    "_render_playbook",
    "_require_backend_allowed",
    "_require_backend_enabled",
    "_reviewer_backend",
    "_fixed_backend",
    "classe_d_execution",
    "env_du_runner",
    "production_indicator_sources",
    "prepare_stage",
    "start_run",
)


def test_stage_module_stays_under_800_lines() -> None:
    lines = len(STAGE.read_text(encoding="utf-8").splitlines())
    assert lines <= MAX_LINES, f"activities/stage.py has {lines} lines: move helpers to stage_*.py"


def test_the_registered_activity_names_are_unchanged() -> None:
    names = sorted(
        definition.name
        for activity in activities.ALL_ACTIVITIES
        if (definition := getattr(activity, "__temporal_activity_definition", None)) is not None
    )
    assert names == json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_patched_names_are_resolved_from_stage() -> None:
    assert stage._resolve_on_enabled_backend.__module__ == "choregos_orchestrator.activities.stage"
    assert stage._resolve_model.__module__ == "choregos_orchestrator.activities.stage"
    assert stage.prepare_stage.__module__ == "choregos_orchestrator.activities.stage"


def test_every_moved_helper_is_still_importable_from_stage() -> None:
    missing = [name for name in IMPORTED_FROM_STAGE if not hasattr(stage, name)]
    assert missing == []
    assert stage._require_backend_enabled.__module__.endswith("stage_backend")
    assert stage._mcp_servers.__module__.endswith("stage_tools")
    assert stage._refuse_on_budget.__module__.endswith("stage_refusals")

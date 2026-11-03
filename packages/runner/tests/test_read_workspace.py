"""A `read` workspace (S51-03, ADR 0119): the repository is cloned, and nothing of it is written.

The orchestrator gives such a run the scope `READ_ONLY_WRITE_PATHS`, never an empty one: to these
guards an empty scope means "no scope declared, write allowed".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from choregos_contracts import Permissions, StageInput
from choregos_core.workspace import READ_ONLY_WRITE_PATHS
from choregos_runner.exits import Exit
from choregos_runner.guardrails import GuardRails
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script, valid_result

READ_ONLY = list(READ_ONLY_WRITE_PATHS)


def test_the_read_only_scope_refuses_every_repository_file_and_keeps_the_deliverables() -> None:
    rails = GuardRails(Permissions(write_paths=READ_ONLY), workspace="/workspace")
    assert not rails.check_write("/workspace/src/orders.py").allowed
    assert not rails.check_write("README.md").allowed
    assert rails.check_write("/workspace/.choregos/outputs/analysis.md").allowed
    assert rails.check_write(".choregos/result.json").allowed
    # Why the scope is never `[]`: that is "no scope declared", and the write goes through.
    assert GuardRails(Permissions(write_paths=[])).check_write("src/orders.py").allowed


async def test_a_read_run_changes_nothing_of_the_repository_and_pushes_nothing(
    stage_input: StageInput, runner_settings: Any
) -> None:
    stage_input.allowed_paths = list(READ_ONLY)
    stage_input.permissions.write_paths = list(READ_ONLY)
    agent_script(
        {
            "turns": [
                {
                    "writes": [
                        {"path": "src/orders.py", "content": "def total(lines):\n    return 0\n"},
                        {"path": ".choregos/outputs/analysis.md", "content": "# Analysis\n"},
                    ],
                    "result": valid_result("analysed"),
                },
                {"result": valid_result("analysed, nothing written")},
            ]
        },
        stage_input,
        runner_settings.workspace,
    )
    client = FakeInternalClient(stage_input)
    outcome = await Runner(runner_settings, client).execute(stage_input, client)  # type: ignore[arg-type]

    assert outcome.exit_code is Exit.OK, outcome.detail
    denials = [
        e for e in client.events if e["type"] == "session/request_permission" and not e["payload"]["allowed"]
    ]
    assert any("orders" in e["payload"]["target"] for e in denials), "the write was refused"
    workspace = Path(runner_settings.workspace)
    assert "return 0" not in (workspace / "src" / "orders.py").read_text(), "the repository file is intact"
    assert outcome.result is not None
    assert outcome.result.artifacts.commits == [], "nothing committed, so nothing pushed"
    assert outcome.result.evidence.diff_files == 0

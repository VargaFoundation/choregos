"""S35-03 — a resumed run reloads its agent's conversation when the agent can (ACP `session/load`).

The sandbox kept the workspace across a person's answer; the agent kept its conversation in its
home, which is the workspace. The runner saved the conversation id there when it opened it. On
the resumed run it reloads it — or, with an agent that does not announce `loadSession`, says so
in the journal and starts a new conversation in the same workspace.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from choregos_contracts import StageInput
from choregos_runner.exits import Exit
from choregos_runner.runner import SAVED_SESSION

from .conftest import valid_result
from .test_runner_flow import execute

TURN = {"turns": [{"writes": [{"path": "src/orders.py", "content": "x = 1\n"}], "result": valid_result()}]}


def _types(client: Any) -> list[str]:
    return [event["type"] for event in client.events]


def _save(workspace: Path, session_id: str, backend: str = "codex") -> None:
    path = workspace / SAVED_SESSION
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"backend": backend, "session_id": session_id}), encoding="utf-8")


async def test_a_new_conversation_is_saved_on_the_workspace_and_never_committed(
    stage_input: StageInput, runner_settings: Any
) -> None:
    outcome, client = await execute(stage_input, runner_settings, TURN)
    assert outcome.exit_code is Exit.OK, outcome.detail
    saved = json.loads((runner_settings.workspace / SAVED_SESSION).read_text(encoding="utf-8"))
    assert saved == {"backend": "codex", "session_id": "sess-fake-1"}
    assert "session/new" in _types(client) and "session/load" not in _types(client)
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=runner_settings.workspace, capture_output=True, text=True, check=True
    ).stdout
    assert "acp-session.json" not in tracked


async def test_a_resumed_run_reloads_the_conversation_when_the_agent_can(
    stage_input: StageInput, runner_settings: Any
) -> None:
    _save(runner_settings.workspace, "sess-before-the-question")
    outcome, client = await execute(stage_input, runner_settings, {**TURN, "load_session": True})
    assert outcome.exit_code is Exit.OK, outcome.detail
    loads = [e for e in client.events if e["type"] == "session/load"]
    assert [e["payload"]["session_id"] for e in loads] == ["sess-before-the-question"]
    assert "session/new" not in _types(client), "the same conversation, not a new one"
    replayed = json.dumps([e for e in client.events if e["type"] == "session/update"])
    assert "history of sess-before-the-question" in replayed, "the agent replayed what it had said"


async def test_an_agent_without_load_session_says_so_and_starts_anew(
    stage_input: StageInput, runner_settings: Any
) -> None:
    _save(runner_settings.workspace, "sess-before-the-question")
    outcome, client = await execute(stage_input, runner_settings, TURN)
    assert outcome.exit_code is Exit.OK, outcome.detail
    types = _types(client)
    assert "session/load.unsupported" in types and "session/new" in types
    assert types.index("session/load.unsupported") < types.index("session/new")
    unsupported = next(e for e in client.events if e["type"] == "session/load.unsupported")
    assert "loadSession" in unsupported["payload"]["detail"]


async def test_a_refused_reload_is_journaled_and_the_run_goes_on(
    stage_input: StageInput, runner_settings: Any
) -> None:
    _save(runner_settings.workspace, "sess-gone")
    outcome, client = await execute(
        stage_input, runner_settings, {**TURN, "load_session": True, "load_fails": True}
    )
    assert outcome.exit_code is Exit.OK, outcome.detail
    types = _types(client)
    assert "session/load.failed" in types and "session/new" in types and "session/load" not in types


async def test_a_conversation_of_another_backend_is_not_reloaded(
    stage_input: StageInput, runner_settings: Any
) -> None:
    _save(runner_settings.workspace, "sess-of-claude", backend="claude-code")
    outcome, client = await execute(stage_input, runner_settings, {**TURN, "load_session": True})
    assert outcome.exit_code is Exit.OK, outcome.detail
    assert "session/load" not in _types(client) and "session/new" in _types(client)

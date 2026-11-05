"""An agent offered tool scripts writes them with its file-write tool in the run's script directory,
and nowhere else outside its workspace (#1365, ADR 0122).

S57-08's instructions told the agent to write its scripts under `$TMPDIR`, but the runner's write
guards read any path outside the workspace as outside the work item's scope: the agent's Write tool
was refused there, and only its shell could do it. `$TMPDIR/choregos/scripts/` is now allowed, when
the run is offered tool scripts, by `GuardRails.check_write` and by Claude Code's scope hook — which
agree, case by case — and by the ACP client's file system. The client's directory beside it, the rest
of `$TMPDIR`, a path with a `.` or `..` segment and a secret-looking file stay refused. Nothing there is
in the workspace, so the scope check of the diff is unchanged: none of it reaches a commit.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import StageInput, ToolScripts
from choregos_runner import tool_scripts
from choregos_runner.acp.client import AcpClient
from choregos_runner.acp.protocol import FS_WRITE_TEXT_FILE, Request, Response
from choregos_runner.backends.claude_code import ClaudeCodeBackend
from choregos_runner.exits import Exit
from choregos_runner.guardrails import GuardRails
from choregos_runner.workspace import agent_home

from .test_runner_flow import execute, valid_result

#: The work item's scope in these tests.
SCOPE = ["src/orders.py"]

#: (case, path, offered, allowed): `{scripts}` is the run's script directory, `{tmp}` the runner's
#: temporary directory, `{ws}` the workspace.
CASES: list[tuple[str, str, bool, bool]] = [
    ("a script, offered", "{scripts}/check.py", True, True),
    ("a script in a subdirectory, offered", "{scripts}/audit/rules.py", True, True),
    ("a script, not offered", "{scripts}/check.py", False, False),
    ("elsewhere under TMPDIR", "{tmp}/check.py", True, False),
    ("the client's directory", "{tmp}/choregos/tools/choregos_tools/__init__.py", True, False),
    ("climbing out of the script directory", "{scripts}/../tools/choregos_tools/__init__.py", True, False),
    ("a dot segment", "{scripts}/./check.py", True, False),
    ("the directory itself", "{scripts}/", True, False),
    ("a secret-looking file", "{scripts}/.env", True, False),
    ("outside everything", "/etc/choregos-check.py", True, False),
    ("the workspace, outside the scope", "{ws}/src/billing.py", True, False),
    ("the workspace, in the scope", "{ws}/src/orders.py", True, True),
]
IDS = [case for case, *_ in CASES]


@pytest.fixture(autouse=True)
def own_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The runner's temporary directory, per test: `$TMPDIR` too, as in a pod."""
    own = tmp_path / "tmp"
    own.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(own))
    monkeypatch.setenv("TMPDIR", str(own))
    return own


def _path(template: str, own_tmp: Path, workspace: Path) -> str:
    scripts = own_tmp / "choregos" / "scripts"
    return template.format(scripts=scripts, tmp=own_tmp, ws=workspace)


def _offer(stage_input: StageInput, offered: bool) -> StageInput:
    stage_input.tools.scripts = ToolScripts() if offered else None
    return stage_input


def _check_write(stage_input: StageInput, workspace: Path, path: str) -> bool:
    rails = GuardRails(
        stage_input.permissions,
        list(SCOPE),
        workspace=str(workspace),
        scripts_dir=tool_scripts.offered_script_directory(stage_input),
    )
    return rails.check_write(path).allowed


def _hook(stage_input: StageInput, workspace: Path, path: str) -> bool:
    """Claude Code's `PreToolUse` scope hook, as the runner writes it for this run, run on a Write."""
    (workspace / ".choregos" / "hooks").mkdir(parents=True, exist_ok=True)
    (workspace / ".choregos" / "allowed_paths.txt").write_text("\n".join(SCOPE) + "\n", encoding="utf-8")
    hook = workspace / ".choregos" / "hooks" / "check_scope.sh"
    hook.write_text(
        ClaudeCodeBackend().launch_plan(stage_input, workspace).files[str(hook.relative_to(workspace))]
    )
    hook.chmod(0o755)
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": path, "content": "print(1)\n"}})
    ran = subprocess.run(
        [str(hook)], input=payload, capture_output=True, text=True, cwd=workspace, check=False
    )
    assert ran.returncode in {0, 2}, ran.stderr
    return ran.returncode == 0


@pytest.mark.parametrize(("case", "template", "offered", "allowed"), CASES, ids=IDS)
def test_check_write_allows_the_script_directory_only_when_offered(
    stage_input: StageInput,
    tmp_path: Path,
    own_tmp: Path,
    case: str,
    template: str,
    offered: bool,
    allowed: bool,
) -> None:
    workspace = tmp_path / "ws"
    path = _path(template, own_tmp, workspace)
    assert _check_write(_offer(stage_input, offered), workspace, path) is allowed, case


@pytest.mark.parametrize(("case", "template", "offered", "allowed"), CASES, ids=IDS)
def test_the_scope_hook_agrees_with_check_write(
    stage_input: StageInput,
    tmp_path: Path,
    own_tmp: Path,
    case: str,
    template: str,
    offered: bool,
    allowed: bool,
) -> None:
    workspace = tmp_path / "ws"
    path = _path(template, own_tmp, workspace)
    _offer(stage_input, offered)
    assert _hook(stage_input, workspace, path) is allowed, case
    assert _hook(stage_input, workspace, path) is _check_write(stage_input, workspace, path), case


async def test_the_acp_file_system_writes_beneath_the_script_directory_and_not_through_a_link(
    tmp_path: Path, own_tmp: Path
) -> None:
    """The client's `fs/write_text_file` (Claude Code's Write goes through it): the script directory
    when it is named, never the client's directory, not even through a link the agent's shell made."""
    workspace, scripts = tmp_path / "ws", own_tmp / "choregos" / "scripts"
    tools = own_tmp / "choregos" / "tools"
    for directory in (workspace, scripts, tools):
        directory.mkdir(parents=True)
    (scripts / "client").symlink_to(tools)
    answers: list[Response] = []

    async def capture(response: Response) -> None:
        answers.append(response)

    async def write(client: AcpClient, path: Path) -> bool:
        await client._handle_request(
            Request(id=1, method=FS_WRITE_TEXT_FILE, params={"path": str(path), "content": "x"})
        )
        return answers[-1].error is None

    offered = AcpClient(["agent"], cwd=str(workspace), scripts_dir=str(scripts))
    offered.respond = capture  # type: ignore[method-assign]
    assert await write(offered, scripts / "check.py") and (scripts / "check.py").read_text() == "x"
    assert not await write(offered, scripts / "client" / "__init__.py"), "a link to the client's directory"
    assert not await write(offered, tools / "__init__.py")
    assert not await write(offered, own_tmp / "check.py")
    assert not (tools / "__init__.py").exists() and not (own_tmp / "check.py").exists()

    not_offered = AcpClient(["agent"], cwd=str(workspace))
    not_offered.respond = capture  # type: ignore[method-assign]
    assert not await write(not_offered, scripts / "other.py")


@pytest.mark.parametrize("offered", [True, False], ids=["offered", "not-offered"])
async def test_the_agents_write_tool_reaches_the_script_directory_through_the_runner(
    stage_input: StageInput, runner_settings: Any, own_tmp: Path, offered: bool
) -> None:
    """End to end: the agent's Write (the permission, then the client's file system) under the runner."""
    scripts = own_tmp / "choregos" / "scripts"
    writes = [
        {"path": str(scripts / "check.py"), "content": "print('checked')\n"},
        {
            "path": str(own_tmp / "choregos" / "tools" / "choregos_tools" / "__init__.py"),
            "content": "x = 1\n",
        },
        {"path": str(own_tmp / "check.py"), "content": "print('elsewhere')\n"},
    ]
    outcome, _ = await execute(
        _offer(stage_input, offered),
        runner_settings,
        {"turns": [{"fs_writes": writes, "result": valid_result()}]},
    )
    assert outcome.exit_code is Exit.OK, outcome.detail
    outcomes = json.loads((agent_home(stage_input) / "fs_writes.json").read_text(encoding="utf-8"))
    assert outcomes == (["written", "refused", "refused"] if offered else ["refused"] * 3)
    assert (scripts / "check.py").is_file() is offered
    assert not (own_tmp / "check.py").exists()
    if offered:
        client_init = own_tmp / "choregos" / "tools" / "choregos_tools" / "__init__.py"
        assert "x = 1" not in client_init.read_text(encoding="utf-8"), "the runner's client, untouched"
        assert (
            "choregos/scripts/" in tool_scripts.INSTRUCTIONS
            and "file-write tool" in tool_scripts.INSTRUCTIONS
        )
    # Outside the workspace: nothing of it in the tree the run commits.
    assert not list(runner_settings.workspace.rglob("check.py"))

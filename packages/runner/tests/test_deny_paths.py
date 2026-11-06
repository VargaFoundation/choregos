"""`scope.deny_paths` bounds the agent's writes, whatever its allowed paths say (C2, D2C plan §4).

Until C2 the policy's `scope.deny_paths` was read only when an agent asked to widen its scope
(`PolicyEngine.path_denied`): an agent could write `.github/workflows/ci.yml` as long as its work item's
scope covered it. The runner now gets the denied paths in `permissions.deny_paths`, and its three write
guards refuse them, case by case alike: `GuardRails.check_write` (the ACP permission request), Claude
Code's `PreToolUse` scope hook, and the ACP client's `fs/write_text_file`. A write that gets through
anyway (a shell command) is reverted by the scope check of the diff, like a write outside the scope.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import Permissions, StageInput
from choregos_runner.acp.client import AcpClient
from choregos_runner.acp.protocol import FS_WRITE_TEXT_FILE, Request, Response
from choregos_runner.backends.claude_code import ClaudeCodeBackend
from choregos_runner.exits import Exit
from choregos_runner.guardrails import GuardRails
from choregos_runner.workspace import agent_home

from .test_runner_flow import execute, valid_result

#: The work item's scope in these tests: wide on purpose, so a denied path sits INSIDE it.
SCOPE = ["src/**", ".github/**", "tests/**"]
#: The project's policy.
DENY = [".choregos/**", ".github/workflows/**", "src/generated/**"]

#: (case, path relative to the workspace, allowed)
CASES: list[tuple[str, str, bool]] = [
    ("in the scope", "src/orders.py", True),
    ("denied inside the scope", "src/generated/client.py", False),
    ("a CI workflow, denied inside the scope", ".github/workflows/ci.yml", False),
    ("beside the denied folder", ".github/CODEOWNERS", True),
    ("outside the scope", "docs/readme.md", False),
    ("the step's result, under a denied `.choregos/**`", ".choregos/result.json", True),
    ("a deliverable", ".choregos/outputs/report.md", True),
    ("climbing out of the deliverables", ".choregos/outputs/../hooks/check_scope.sh", False),
    ("the platform's own file", ".choregos/allowed_paths.txt", False),
    ("a secret-looking file in the scope", "src/.env", False),
]
IDS = [case for case, *_ in CASES]


def _permissions(deny: list[str]) -> Permissions:
    return Permissions(write_paths=list(SCOPE), deny_paths=list(deny))


def _with(stage_input: StageInput, deny: list[str]) -> StageInput:
    stage_input.allowed_paths = list(SCOPE)
    stage_input.permissions = _permissions(deny)
    return stage_input


def _check_write(workspace: Path, path: str, deny: list[str]) -> bool:
    return GuardRails(_permissions(deny), list(SCOPE), workspace=str(workspace)).check_write(path).allowed


def _hook(stage_input: StageInput, workspace: Path, path: str) -> bool:
    """Claude Code's scope hook, as the runner writes it for this run, run on a Write of `path`."""
    files = ClaudeCodeBackend().launch_plan(stage_input, workspace).files
    hook = workspace / ".choregos" / "hooks" / "check_scope.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(files[".choregos/hooks/check_scope.sh"], encoding="utf-8")
    hook.chmod(0o755)
    (workspace / ".choregos" / "allowed_paths.txt").write_text("\n".join(SCOPE) + "\n", encoding="utf-8")
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": path, "content": "x = 1\n"}})
    ran = subprocess.run(
        [str(hook)], input=payload, capture_output=True, text=True, cwd=workspace, check=False
    )
    assert ran.returncode in {0, 2}, ran.stderr
    return ran.returncode == 0


async def _fs_write(workspace: Path, path: str, deny: list[str]) -> bool:
    """The ACP client's `fs/write_text_file`, guarded as the runner guards it."""
    rails = GuardRails(_permissions(deny), list(SCOPE), workspace=str(workspace))

    def guard(target: str) -> tuple[bool, str]:
        decision = rails.check_write(target)
        return decision.allowed, decision.reason

    answers: list[Response] = []

    async def capture(response: Response) -> None:
        answers.append(response)

    client = AcpClient(["agent"], cwd=str(workspace), write_guard=guard)
    client.respond = capture  # type: ignore[method-assign]
    await client._handle_request(
        Request(id=1, method=FS_WRITE_TEXT_FILE, params={"path": path, "content": "x"})
    )
    written = answers[-1].error is None
    target = Path(path)
    assert written is (target.is_file() and target.read_text(encoding="utf-8") == "x"), path
    return written


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path


@pytest.mark.parametrize(("case", "relative", "allowed"), CASES, ids=IDS)
def test_check_write_refuses_a_denied_path_inside_the_scope(
    workspace: Path, case: str, relative: str, allowed: bool
) -> None:
    assert _check_write(workspace, f"{workspace}/{relative}", DENY) is allowed, case


@pytest.mark.parametrize(("case", "relative", "allowed"), CASES, ids=IDS)
async def test_the_three_write_guards_agree(
    stage_input: StageInput, workspace: Path, case: str, relative: str, allowed: bool
) -> None:
    """The permission request, Claude Code's hook and the ACP file system answer alike, case by case."""
    absolute = f"{workspace}/{relative}"
    rails = _check_write(workspace, absolute, DENY)
    hook = _hook(_with(stage_input, DENY), workspace, absolute)
    file_system = await _fs_write(workspace, absolute, DENY)
    assert (rails, hook, file_system) == (allowed, allowed, allowed), case


def test_a_denied_path_is_written_without_the_policy(workspace: Path) -> None:
    """The cases above refuse because of `deny_paths`: without it, the scope alone lets them through."""
    for relative in ("src/generated/client.py", ".github/workflows/ci.yml"):
        assert _check_write(workspace, f"{workspace}/{relative}", []) is True, relative


def test_the_refusal_names_the_policy_and_no_scope_change_grants_it(workspace: Path) -> None:
    rails = GuardRails(_permissions(DENY), list(SCOPE), workspace=str(workspace))
    rails.extend_scope([".github/workflows/**"])  # a widening the API would never grant, granted anyway
    decision = rails.check_write(f"{workspace}/.github/workflows/ci.yml")
    assert decision.allowed is False
    assert "scope.deny_paths" in decision.reason and "`.github/workflows/**`" in decision.reason


def test_the_task_lists_the_forbidden_paths(stage_input: StageInput) -> None:
    from choregos_runner.workspace import _task_markdown

    task = _task_markdown(_with(stage_input, DENY))
    assert "## Forbidden paths" in task and "- `.github/workflows/**`" in task


async def test_the_runner_refuses_a_denied_write_at_each_door_and_reverts_one_made_anyway(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """End to end: a Write asked for (refused at the permission), a file-system write made without asking
    (refused by the client), and a shell write nobody asked about (reverted by the scope check)."""
    workflow = ".github/workflows/ci.yml"
    _with(stage_input, [*DENY, "src/billing.py"])
    workspace = Path(runner_settings.workspace)
    outcome, client = await execute(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "fs_writes": [
                        {"path": f"{workspace}/src/generated/client.py", "content": "x = 1\n"},
                        {"path": f"{workspace}/{workflow}", "content": "on: push\n", "ask": False},
                        {
                            "path": f"{workspace}/src/orders.py",
                            "content": "def total(lines):\n    return 0\n",
                        },
                    ],
                    "unasked": [{"write": f"{workspace}/src/billing.py"}],
                    "result": valid_result(),
                },
                {"result": valid_result("scope respected")},
            ]
        },
    )
    assert outcome.exit_code is Exit.OK, outcome.detail
    outcomes = json.loads((agent_home(stage_input) / "fs_writes.json").read_text(encoding="utf-8"))
    assert outcomes[0] == "refused", outcomes
    assert "scope.deny_paths" in outcomes[1], outcomes
    assert outcomes[2] == "written", outcomes
    assert not (workspace / workflow).exists() and not (workspace / "src" / "generated").exists()
    assert (workspace / "src" / "billing.py").read_text() == "RATE = 0.2\n", "the shell's write is reverted"
    reverted = [e["payload"]["paths"] for e in client.events if e["type"] == "scope.reverted"]
    assert reverted == [["src/billing.py"]], reverted

"""A hosted agent that works in the repository (S41-18, ADR 0080 §7).

The container gets a copy of the working tree, never the checkout: no `.git`, no credential. Under
`write`, what it changed comes back one file at a time through the guard rails, and the runner
commits and pushes it; a write the rails refuse, a `.git` path, a symlink out of the tree and the
runner's own files never do. `none` and `read` push nothing. Under `write` no project command runs:
it would run the container's code beside the run's credentials.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from choregos_adapters.fakes import FakeHostedAgent
from choregos_contracts import HostedCall, StageInput, StageStatus
from choregos_runner import hosted_workspace
from choregos_runner.exits import Exit
from choregos_runner.workspace import Workspace

from .conftest import FakeInternalClient, git
from .test_hosted_step import IMAGE, _events, _hosted, _run

pytestmark = pytest.mark.asyncio

BRANCH = "choregos/1-total"


def _working(stage_input: StageInput, workspace: str, **update: Any) -> StageInput:
    """A hosted step that keeps the repository, as the orchestrator sends it under `read`/`write`."""
    hosted = HostedCall(image=IMAGE, agent_slug="triage", agent_version=3, workspace=workspace)  # type: ignore[arg-type]
    return _hosted(stage_input, hosted=hosted, repo=stage_input.repo, **update)


@pytest.fixture
def shared(tmp_path: Path) -> Path:
    path = tmp_path / "shared"
    path.mkdir()
    return path


@pytest.fixture
def settings(runner_settings: Any, shared: Path) -> Any:
    return runner_settings.model_copy(update={"hosted_workspace": shared})


async def _go(
    stage: StageInput, script: list[tuple[Any, ...]], settings: Any, shared: Path
) -> tuple[Any, FakeInternalClient, FakeHostedAgent]:
    assert stage.hosted is not None
    agent = FakeHostedAgent(
        script=script,
        answer="Done.",
        workspace=shared if stage.hosted.workspace != "none" else None,
    )
    outcome, client = await _run(stage, agent, settings)
    return outcome, client, agent


def _pushed(origin: Path, path: str) -> str | None:
    """`path` on the run's branch, as the origin has it; `None` when absent (or no branch)."""
    shown = subprocess.run(
        ["git", "--git-dir", str(origin), "show", f"{BRANCH}:{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return shown.stdout if shown.returncode == 0 else None


def _branch_exists(origin: Path) -> bool:
    found = subprocess.run(
        ["git", "--git-dir", str(origin), "rev-parse", "--verify", "-q", BRANCH],
        capture_output=True,
        check=False,
    )
    return found.returncode == 0


async def test_what_the_container_writes_is_committed_and_pushed(
    stage_input: StageInput, settings: Any, shared: Path, toy_repo: Path
) -> None:
    fixed = "def total(lines):\n    return sum(lines) - 0\n"
    script = [
        ("read", "src/orders.py"),
        ("write", "src/orders.py", fixed),
        ("write", "tests/test_total.py", "def test_total():\n    pass\n"),
    ]
    outcome, client, agent = await _go(_working(stage_input, "write"), script, settings, shared)

    assert outcome.exit_code is Exit.OK and client.results[-1].status is StageStatus.DONE
    assert agent.seen[0] == "def total(lines):\n    return sum(lines)\n", "the container sees the tree"
    assert agent.env["CHOREGOS_WORKSPACE"] == "/workspace"
    assert _pushed(toy_repo, "src/orders.py") == fixed
    assert _pushed(toy_repo, "tests/test_total.py") is not None
    assert _events(client, "hosted.workspace.imported") == [
        {"written": ["src/orders.py", "tests/test_total.py"], "deleted": []}
    ]
    assert client.results[-1].artifacts.commits, "the runner committed it"
    assert client.results[-1].diagnostics.permission_denials == 0


async def test_the_container_gets_no_git_and_none_of_the_runners_files(
    stage_input: StageInput, settings: Any, shared: Path
) -> None:
    script = [("read", ".git/config"), ("read", ".choregos/task.md")]
    _, client, agent = await _go(_working(stage_input, "read"), script, settings, shared)

    assert agent.seen[0] is None, "no `.git`: the history and the remote stay the runner's"
    assert agent.seen[1] and "Corriger le total" in agent.seen[1], "the task is there to read"
    assert not (shared / ".git").exists()
    assert not (shared / ".choregos" / "transcript.jsonl").exists()
    files = [path for path in shared.rglob("*") if path.is_file()]
    assert files and all(b"tok" not in path.read_bytes() for path in files), "no run token"
    assert _events(client, "hosted.workspace") == [{"mode": "read", "files": len(files)}]


async def test_under_read_nothing_the_container_writes_comes_back(
    stage_input: StageInput, settings: Any, shared: Path, toy_repo: Path
) -> None:
    script = [("write", "src/orders.py", "broken\n")]
    _, client, _ = await _go(_working(stage_input, "read"), script, settings, shared)

    assert client.results[-1].status is StageStatus.DONE
    assert not _branch_exists(toy_repo), "nothing changed in the checkout: nothing pushed"
    assert _events(client, "hosted.workspace.imported") == []


async def test_under_none_there_is_no_tree_and_nothing_is_pushed(
    stage_input: StageInput, settings: Any, shared: Path, toy_repo: Path
) -> None:
    _, client, agent = await _go(_hosted(stage_input), [("write", "src/orders.py", "x\n")], settings, shared)

    assert agent.seen == ["no workspace"] and "CHOREGOS_WORKSPACE" not in agent.env
    assert list(shared.iterdir()) == []
    assert not _branch_exists(toy_repo)
    assert _events(client, "hosted.workspace") == []


@pytest.mark.parametrize(
    ("action", "path", "why"),
    [
        (("write", "src/billing.py", "RATE = 0.9\n"), "src/billing.py", "outside the allowed paths"),
        (("write", "tests/.env", "TOKEN=x\n"), "tests/.env", "sensitive file"),
        (("symlink", "tests/leak", "/proc/self/environ"), "tests/leak", "absolute path"),
        (("symlink", "tests/up", "../.."), "tests/up", "out of the repository"),
        (("write", "tests/.git/config", "[core]\n"), "tests/.git", "`.git` path"),
        (("write", ".choregos/result.json", "{}"), ".choregos/result.json", "only the files of"),
        (("write", ".choregos/task.md", "do else"), ".choregos/task.md", "only the files of"),
        (("fifo", "tests/fifo"), "tests/fifo", "not a regular file"),
    ],
)
async def test_a_write_the_guard_refuses_stays_in_the_container(
    stage_input: StageInput,
    settings: Any,
    shared: Path,
    toy_repo: Path,
    action: tuple[Any, ...],
    path: str,
    why: str,
) -> None:
    script = [action, ("write", "tests/test_ok.py", "pass\n")]
    _, client, _ = await _go(_working(stage_input, "write"), script, settings, shared)

    refused = _events(client, "hosted.write_refused")
    assert [event["path"] for event in refused] == [path]
    assert why in refused[0]["reason"]
    assert _pushed(toy_repo, "tests/test_ok.py") == "pass\n", "the step's other work still comes back"
    assert _pushed(toy_repo, path) in (None, "RATE = 0.2\n"), "never the container's version"
    assert client.results[-1].diagnostics.permission_denials == 1


async def test_a_deletion_is_mirrored_within_the_scope_only(
    stage_input: StageInput, settings: Any, shared: Path, toy_repo: Path
) -> None:
    script = [("delete", "src/orders.py"), ("delete", "src/billing.py")]
    _, client, _ = await _go(_working(stage_input, "write"), script, settings, shared)

    assert _pushed(toy_repo, "src/orders.py") is None, "deleted, and the deletion pushed"
    assert _pushed(toy_repo, "src/billing.py") == "RATE = 0.2\n"
    assert [event["path"] for event in _events(client, "hosted.write_refused")] == ["src/billing.py"]


def _seed(toy_repo: Path, tmp_path: Path, files: dict[str, str], link: tuple[str, str] | None = None) -> None:
    """Pushes `files` (and a symlink `link`) to the toy repository's `main`, before the run clones it."""
    seed = tmp_path / "seeding"
    git("clone", str(toy_repo), str(seed), cwd=tmp_path)
    for path, text in files.items():
        (seed / path).parent.mkdir(parents=True, exist_ok=True)
        (seed / path).write_text(text)
    if link is not None:
        (seed / link[0]).parent.mkdir(parents=True, exist_ok=True)
        (seed / link[0]).symlink_to(link[1])
    git("-c", "user.name=s", "-c", "user.email=s@t", "add", "-A", cwd=seed)
    git("-c", "user.name=s", "-c", "user.email=s@t", "commit", "-m", "seed", cwd=seed)
    git("push", "origin", "main", cwd=seed)


async def test_a_write_through_a_symlink_of_the_repository_is_refused(
    stage_input: StageInput, settings: Any, shared: Path, toy_repo: Path, tmp_path: Path
) -> None:
    """`tests/lib -> ../src` in the repository: `tests/lib/billing.py` is in scope by its name and
    lands in `src/billing.py`, which is not. The guard judges the path the write really takes."""
    _seed(toy_repo, tmp_path, {}, link=("tests/lib", "../src"))
    # The link itself is out of scope, so it stays; what is written under its name is in scope.
    scope = ["tests/lib/*.py"]
    permissions = stage_input.permissions.model_copy(update={"write_paths": scope})
    stage = _working(stage_input, "write", permissions=permissions, allowed_paths=scope)
    # In the copy the container replaces the link by a directory, then writes into it.
    script = [("delete", "tests/lib"), ("write", "tests/lib/billing.py", "RATE = 0.9\n")]
    _, client, _ = await _go(stage, script, settings, shared)

    reasons = {event["path"]: event["reason"] for event in _events(client, "hosted.write_refused")}
    assert "symlink of the repository" in reasons["tests/lib/billing.py"]
    assert _pushed(toy_repo, "src/billing.py") in (None, "RATE = 0.2\n")
    checkout = settings.workspace / "src" / "billing.py"
    assert checkout.read_text() == "RATE = 0.2\n", "the runner's checkout is untouched"


async def test_a_directory_of_the_repository_never_becomes_a_file(
    stage_input: StageInput, settings: Any, shared: Path, toy_repo: Path, tmp_path: Path
) -> None:
    """The container may replace a directory by a file; the checkout keeps the directory while it
    holds a file the step may not delete."""
    _seed(toy_repo, tmp_path, {"tests/data/sample.csv": "a,b\n"})
    scope = ["tests/data", "tests/test_ok.py"]
    permissions = stage_input.permissions.model_copy(update={"write_paths": scope})
    stage = _working(stage_input, "write", permissions=permissions, allowed_paths=scope)
    script = [
        ("delete", "tests/data"),
        ("write", "tests/data", "flat\n"),
        ("write", "tests/test_ok.py", "pass\n"),
    ]
    _, client, _ = await _go(stage, script, settings, shared)

    reasons = {event["path"]: event["reason"] for event in _events(client, "hosted.write_refused")}
    assert "outside the allowed paths" in reasons["tests/data/sample.csv"]
    assert "cannot become a file" in reasons["tests/data"]
    assert _pushed(toy_repo, "tests/data/sample.csv") == "a,b\n"
    assert _pushed(toy_repo, "tests/test_ok.py") == "pass\n"


async def test_what_the_repository_ignores_is_neither_refused_nor_brought_back(
    stage_input: StageInput, settings: Any, shared: Path, toy_repo: Path, tmp_path: Path
) -> None:
    """`__pycache__/` a container's Python leaves behind is not a write out of scope."""
    _seed(toy_repo, tmp_path, {".gitignore": "__pycache__/\n"})
    script = [
        ("write", "src/__pycache__/billing.cpython-312.pyc", "bytecode"),
        ("write", "tests/__pycache__/test_ok.cpython-312.pyc", "bytecode"),
        ("write", "tests/test_ok.py", "pass\n"),
    ]
    _, client, _ = await _go(_working(stage_input, "write"), script, settings, shared)

    assert _events(client, "hosted.write_refused") == []
    assert _events(client, "hosted.workspace.imported")[0]["written"] == ["tests/test_ok.py"]
    assert client.results[-1].diagnostics.permission_denials == 0


async def test_a_deliverable_comes_back_from_the_outputs(
    stage_input: StageInput, settings: Any, shared: Path
) -> None:
    script = [("write", ".choregos/outputs/report.md", "# Report\n")]
    _, client, _ = await _go(_working(stage_input, "write"), script, settings, shared)

    assert _events(client, "hosted.workspace.imported")[0]["written"] == [".choregos/outputs/report.md"]
    assert (settings.workspace / ".choregos" / "outputs" / "report.md").read_text() == "# Report\n"


async def test_under_write_no_project_command_runs(
    stage_input: StageInput, settings: Any, shared: Path
) -> None:
    """The project's commands would run the container's code inside the runner, beside the run's
    token and keys: none runs, and the step says so."""
    project = stage_input.project.model_copy(
        update={"test_command": "touch RAN", "lint_command": "touch RAN", "typecheck_command": "touch RAN"}
    )
    stage = _working(stage_input, "write")
    stage = stage.model_copy(update={"project": project})
    _, client, _ = await _go(stage, [("write", "tests/x.py", "pass\n")], settings, shared)

    assert not (settings.workspace / "RAN").exists()
    assert len(_events(client, "checks.skipped")) == 1
    evidence = client.results[-1].evidence
    assert evidence.tests_passed is None and evidence.lint == "skipped"


async def test_under_read_the_fact_commands_still_run(
    stage_input: StageInput, settings: Any, shared: Path
) -> None:
    project = stage_input.project.model_copy(update={"test_command": "touch RAN"})
    stage = _working(stage_input, "read").model_copy(update={"project": project})
    _, client, _ = await _go(stage, [], settings, shared)

    assert _events(client, "checks.skipped") == []
    assert not (settings.workspace / "RAN").exists(), "a hosted step never runs the test command"


@pytest.mark.parametrize("reverts", [True, False])
async def test_the_scope_check_still_catches_what_the_guard_let_through(
    stage_input: StageInput,
    settings: Any,
    shared: Path,
    toy_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    reverts: bool,
) -> None:
    """The net behind the copy back, as behind an ACP agent: with the guard bypassed, an out-of-scope
    write is reverted before the commit, and one that cannot be reverted blocks the step."""
    monkeypatch.setattr(hosted_workspace, "_refused_destination", lambda *_: "")
    if not reverts:

        async def _no_revert(self: Workspace, paths: list[str], base: str) -> list[str]:
            return []

        monkeypatch.setattr(Workspace, "revert_paths", _no_revert)
    script = [("write", "src/billing.py", "RATE = 0.9\n"), ("write", "tests/test_ok.py", "pass\n")]
    _, client, _ = await _go(_working(stage_input, "write"), script, settings, shared)

    result = client.results[-1]
    if reverts:
        assert _events(client, "scope.reverted") == [{"paths": ["src/billing.py"]}]
        assert _pushed(toy_repo, "src/billing.py") == "RATE = 0.2\n"
        assert result.status is StageStatus.DONE
    else:
        assert result.status is StageStatus.BLOCKED and result.reason == "scope"

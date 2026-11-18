# SPDX-License-Identifier: Apache-2.0
"""The runner's git reads nothing the agent wrote (ADR 0146 §5, S65-04c).

#1345 neutralised each execution vector by a `-c` override. This file proves the other half: the
repository the runner commits and pushes with is NOT `<tree>/.git`. The overrides are switched off
here on purpose, so that what keeps a planted hook, `core.fsmonitor`, `includeIf`, filter or link
from running is the location of the git directory alone, and the tests are red if it comes back into
the tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from tranibor_contracts import StageInput, StageStatus
from tranibor_runner import workspace as workspace_module
from tranibor_runner.exits import Exit
from tranibor_runner.runner import Runner
from tranibor_runner.workspace import Workspace, git_dir_for

from .conftest import FakeInternalClient, agent_script, valid_result

pytestmark = pytest.mark.asyncio

#: Built at run time: a literal that looks like a token would trip the secrets scan of the CI.
GIT_TOKEN = "-".join(["glpat", "s65", "04c", "secret"])


@pytest.fixture(autouse=True)
def _overrides_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `-c` hardening and no driver sweep: only the location of the git directory protects."""
    monkeypatch.setattr(workspace_module, "RUNNER_GIT_HARDENING", ())

    async def none(self: Workspace) -> list[str]:
        return []

    monkeypatch.setattr(Workspace, "_driver_overrides", none)


def _plant(markers: Path) -> list[str]:
    """What an agent's shell does: hooks, `core.fsmonitor`, a filter on every file, an `includeIf`
    and a `core.hooksPath` in `.git/config`; each program writes a marker under `markers`."""
    return [
        "sh",
        "-c",
        f'm="{markers}"; mkdir -p "$m"; d="$PWD/.git"; mkdir -p "$d/hooks" "$d/evilhooks" "$d/info"; '
        "for h in pre-commit pre-push post-checkout post-commit reference-transaction; do "
        'printf \'#!/bin/sh\\n: > "%s/%s"\\n\' "$m" "$h" > "$d/hooks/$h"; '
        'cp "$d/hooks/$h" "$d/evilhooks/$h"; chmod +x "$d/hooks/$h" "$d/evilhooks/$h"; done; '
        'printf \'[core]\\n\\tfsmonitor = sh -c ": > %s/fsmonitor; exit 0"\\n'
        '\\thooksPath = %s/.git/evilhooks\\n[filter "evil"]\\n\\tclean = sh -c ": > %s/clean; cat"\\n'
        '\\tsmudge = sh -c ": > %s/smudge; cat"\\n[include]\\n\\tpath = %s/extra\\n\' '
        '"$m" "$PWD" "$m" "$m" "$m" >> "$d/config"; '
        'printf \'[core]\\n\\tsshCommand = sh -c ": > %s/ssh"\\n\' "$m" > "$m/extra"; '
        "printf '* filter=evil\\n' > \"$d/info/attributes\"",
    ]


async def _run(
    stage_input: StageInput, settings: Any, script: dict[str, Any]
) -> tuple[Any, FakeInternalClient]:
    agent_script(script, stage_input, settings.workspace)
    client = FakeInternalClient(stage_input)
    client.git_token = GIT_TOKEN
    outcome = await Runner(settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client


def _ls_tree(repository: Path, ref: str) -> list[str]:
    done = subprocess.run(
        ["git", "--git-dir", str(repository), "ls-tree", "-r", "--name-only", ref],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.splitlines()


async def test_planted_hooks_config_filters_and_includes_are_never_run(
    stage_input: StageInput, runner_settings: Any, toy_repo: Path, tmp_path: Path
) -> None:
    markers = tmp_path / "markers"
    outcome, client = await _run(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "writes": [
                        {"path": "src/orders.py", "content": "def total(lines):\n    return sum(lines) - 1\n"}
                    ],
                    "runs": [
                        _plant(markers),
                        ["sh", "-c", "echo 'RATE = 0.99' > src/billing.py"],
                    ],
                    "result": valid_result(),
                },
                {"result": valid_result("scope respected")},
            ]
        },
    )

    assert outcome.exit_code is Exit.OK, outcome.detail
    assert outcome.result is not None and outcome.result.status is StageStatus.DONE
    assert outcome.result.artifacts.commits, "the run committed and pushed"
    assert [e for e in client.events if e["type"] == "scope.reverted"], "the scope check ran its checkout"
    assert (markers / "extra").exists(), "the agent's shell did plant its files"
    ran = sorted(p.name for p in markers.iterdir() if p.name != "extra")
    assert ran == [], f"the runner ran a program the agent wrote: {ran}"
    pushed = _ls_tree(toy_repo, "refs/heads/choregos/1-total")
    assert not [path for path in pushed if path == ".git" or path.startswith(".git/")], pushed
    assert "src/orders.py" in pushed


async def test_the_runners_git_directory_is_not_in_the_tree(
    stage_input: StageInput, runner_settings: Any
) -> None:
    outcome, _client = await _run(stage_input, runner_settings, {"turns": [{"result": valid_result()}]})
    assert outcome.exit_code is Exit.OK, outcome.detail
    workspace = Path(runner_settings.workspace)
    own = git_dir_for(workspace)
    assert (own / "HEAD").is_file()
    assert workspace.resolve() not in own.resolve().parents


async def test_a_git_dir_replaced_by_a_link_is_not_followed(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    """The agent swaps `.git` for a link to a directory that carries a hook and a config."""
    outside = tmp_path / "outside"
    markers = tmp_path / "markers"
    markers.mkdir()
    swap = [
        "sh",
        "-c",
        f'rm -rf .git; mkdir -p "{outside}/hooks"; ln -s "{outside}" .git; '
        f'printf \'#!/bin/sh\\n: > "{markers}/hook"\\n\' > "{outside}/hooks/pre-commit"; '
        f'chmod +x "{outside}/hooks/pre-commit"; '
        f'printf \'[core]\\n\\tfsmonitor = sh -c ": > {markers}/fsmonitor"\\n\' > "{outside}/config"',
    ]
    outcome, _client = await _run(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "writes": [
                        {"path": "src/orders.py", "content": "def total(lines):\n    return sum(lines) - 1\n"}
                    ],
                    "runs": [swap],
                    "result": valid_result(),
                }
            ]
        },
    )
    assert outcome.exit_code is Exit.OK, outcome.detail
    assert outcome.result is not None and outcome.result.artifacts.commits
    assert list(markers.iterdir()) == []
    assert sorted(p.name for p in outside.iterdir()) == ["config", "hooks"], (
        "nothing written through the link"
    )


async def test_a_link_in_the_tree_is_committed_as_a_link_and_never_read(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path, toy_repo: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-OUTSIDE-THE-TREE\n", encoding="utf-8")
    outcome, _client = await _run(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "writes": [
                        {"path": "src/orders.py", "content": "def total(lines):\n    return sum(lines) - 1\n"}
                    ],
                    "runs": [
                        [
                            "sh",
                            "-c",
                            f'mkdir -p tests; ln -s "{secret}" tests/leak; ln -s "{secret}" .gitattributes; '
                            f'ln -s "{secret}" .gitignore',
                        ]
                    ],
                    "result": valid_result(),
                }
            ]
        },
    )
    assert outcome.exit_code is Exit.OK, outcome.detail
    blob = subprocess.run(
        ["git", "--git-dir", str(toy_repo), "cat-file", "-p", "refs/heads/choregos/1-total:tests/leak"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert blob.strip() == str(secret), "the link is committed as a link"
    assert secret.read_text(encoding="utf-8") == "TOP-SECRET-OUTSIDE-THE-TREE\n"


async def test_the_agents_git_works_and_holds_no_credential(
    stage_input: StageInput, runner_settings: Any, toy_repo: Path
) -> None:
    workspace = Workspace(Path(runner_settings.workspace))
    await workspace.prepare(stage_input, token=GIT_TOKEN)
    seeded = workspace.path / ".git"
    assert seeded.is_dir() and not seeded.is_symlink()
    assert not (seeded / "hooks").exists()
    for path in seeded.rglob("*"):
        if path.is_file():
            blob = path.read_bytes()
            assert GIT_TOKEN.encode() not in blob, path
    config = (seeded / "config").read_text(encoding="utf-8")
    assert "worktree" not in config
    status = subprocess.run(
        ["git", "-C", str(workspace.path), "status", "--porcelain", "--branch"],
        capture_output=True,
        text=True,
        check=True,
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": "/usr/bin:/bin"},
    )
    assert "choregos/1-total" in status.stdout


async def test_a_replay_removes_a_planted_link_without_following_it(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("kept", encoding="utf-8")
    workspace_path = Path(runner_settings.workspace)
    workspace_path.mkdir(parents=True)
    (workspace_path / ".git").symlink_to(outside)
    await Workspace(workspace_path).prepare(stage_input, token=GIT_TOKEN)
    assert (outside / "keep").read_text(encoding="utf-8") == "kept"
    assert not (workspace_path / ".git").is_symlink()

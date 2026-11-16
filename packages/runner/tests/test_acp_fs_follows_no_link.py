# SPDX-License-Identifier: Apache-2.0
"""S65-04b — the ACP `fs/*` handlers follow no link in the tree the agent writes (ADR 0146 §6).

The handlers used to resolve the path (links followed) and then test it was inside the workspace: a link
swapped in after that test carried the runner, its uid and its mounts, outside. They now open the path
through `safe_files`, one component at a time, `O_NOFOLLOW`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from choregos_runner.acp.client import AcpClient
from choregos_runner.acp.protocol import FS_READ_TEXT_FILE, FS_WRITE_TEXT_FILE, Request, Response

SECRET = "SECRET-BEHIND-THE-LINK"


class Harness:
    def __init__(self, workspace: Path, guard: object = None) -> None:
        self.client = AcpClient(["agent"], cwd=str(workspace))
        self.client.write_guard = guard  # type: ignore[assignment]
        self.answers: list[Response] = []

        async def capture(response: Response) -> None:
            self.answers.append(response)

        self.client.respond = capture  # type: ignore[method-assign]

    async def call(self, method: str, **params: object) -> Response:
        await self.client._handle_request(Request(id=1, method=method, params=params))
        return self.answers[-1]

    async def read(self, path: Path | str) -> Response:
        return await self.call(FS_READ_TEXT_FILE, path=str(path))

    async def write(self, path: Path | str, content: str = "pwned") -> Response:
        return await self.call(FS_WRITE_TEXT_FILE, path=str(path), content=content)


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(workspace, a directory outside it, a secret file outside it)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text(SECRET, encoding="utf-8")
    return workspace, outside, secret


async def test_a_plain_file_is_read_and_written(tree: tuple[Path, Path, Path]) -> None:
    workspace, _, _ = tree
    harness = Harness(workspace)
    assert (await harness.write("src/a.txt", "one\ntwo\n")).error is None
    assert (workspace / "src" / "a.txt").read_text() == "one\ntwo\n"
    assert (await harness.read(workspace / "src" / "a.txt")).result == {"content": "one\ntwo\n"}
    assert (await harness.call(FS_READ_TEXT_FILE, path="src/a.txt", line=2, limit=1)).result == {
        "content": "two\n"
    }


async def test_a_link_to_a_system_file_is_not_read(tree: tuple[Path, Path, Path]) -> None:
    workspace, _, _ = tree
    (workspace / "passwd").symlink_to("/etc/passwd")
    answer = await Harness(workspace).read("passwd")
    assert answer.error is not None and answer.result is None
    assert "root:" not in str(answer)


async def test_a_link_to_a_runner_file_is_neither_read_nor_overwritten(
    tree: tuple[Path, Path, Path],
) -> None:
    workspace, _, secret = tree
    (workspace / "token").symlink_to(secret)
    harness = Harness(workspace)
    assert (await harness.read("token")).error is not None
    assert (await harness.write("token")).error is not None
    assert secret.read_text() == SECRET


async def test_a_link_to_a_file_inside_the_tree_is_not_followed_either(tree: tuple[Path, Path, Path]) -> None:
    workspace, _, _ = tree
    (workspace / "real.txt").write_text("real")
    (workspace / "alias.txt").symlink_to("real.txt")
    harness = Harness(workspace)
    assert (await harness.read("alias.txt")).error is not None
    assert (await harness.write("alias.txt")).error is not None
    assert (workspace / "real.txt").read_text() == "real"


async def test_a_link_in_a_middle_directory_is_not_crossed(tree: tuple[Path, Path, Path]) -> None:
    workspace, outside, secret = tree
    (workspace / "dir").symlink_to(outside, target_is_directory=True)
    harness = Harness(workspace)
    assert (await harness.read("dir/secret.txt")).error is not None
    assert (await harness.write("dir/new.txt")).error is not None
    assert (await harness.write(workspace / "dir" / "deeper" / "new.txt")).error is not None
    assert not (outside / "new.txt").exists() and not (outside / "deeper").exists()
    assert secret.read_text() == SECRET


async def test_a_dangling_link_is_not_written_through(tree: tuple[Path, Path, Path]) -> None:
    workspace, outside, _ = tree
    (workspace / "later").symlink_to(outside / "created.txt")
    assert (await Harness(workspace).write("later")).error is not None
    assert not (outside / "created.txt").exists()


async def test_the_paths_that_climb_out_are_still_refused(tree: tuple[Path, Path, Path]) -> None:
    workspace, _, secret = tree
    harness = Harness(workspace)
    for path in ("../outside/secret.txt", str(secret), "a/../../outside/secret.txt", "/etc/passwd", ""):
        assert (await harness.read(path)).error is not None, path
        assert (await harness.write(path)).error is not None, path
    assert secret.read_text() == SECRET


async def test_a_fifo_is_not_opened(tree: tuple[Path, Path, Path]) -> None:
    workspace, _, _ = tree
    os.mkfifo(workspace / "pipe")
    assert (await Harness(workspace).read("pipe")).error is not None


async def test_a_link_swapped_in_after_the_check_is_refused_by_the_open(
    tree: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race the old resolve-then-open lost: the file is regular when the path is checked, a link when
    it is opened. The guard runs between the two (it is where the check is), and the swap happens there."""
    workspace, _, secret = tree
    (workspace / "f.txt").write_text("regular")

    def guard(_: str) -> tuple[bool, str]:
        (workspace / "f.txt").unlink()
        (workspace / "f.txt").symlink_to(secret)
        return True, ""

    harness = Harness(workspace, guard)
    assert (await harness.write("f.txt")).error is not None
    assert secret.read_text() == SECRET

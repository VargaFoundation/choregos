"""The launcher (ADR 0146): a process of the agent's container, started over loopback."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from tranibor_runner import launcher as lc

from .conftest import free_port


@pytest.fixture
async def launcher() -> AsyncIterator[lc.Launcher]:
    port = free_port()
    old = os.umask(0o022)
    server = await asyncio.start_server(lc.handle, "127.0.0.1", port)
    try:
        yield lc.Launcher("127.0.0.1", port)
    finally:
        server.close()
        await server.wait_closed()
        os.umask(old)


async def test_a_command_runs_and_its_streams_and_exit_code_come_back(
    launcher: lc.Launcher, tmp_path: Path
) -> None:
    process = await launcher.spawn(
        ["sh", "-c", "echo out; echo err >&2; echo $FOO; pwd; exit 3"],
        cwd=str(tmp_path),
        env={"FOO": "bar", "PATH": os.environ["PATH"]},
    )
    out, err = await process.communicate()
    assert out.decode().split() == ["out", "bar", str(tmp_path)]
    assert err.decode().strip() == "err"
    assert process.returncode == 3
    await process.aclose()


async def test_a_string_is_run_by_a_shell(launcher: lc.Launcher, tmp_path: Path) -> None:
    process = await launcher.spawn("echo a && echo b", cwd=str(tmp_path), env={"PATH": os.environ["PATH"]})
    out, _ = await process.communicate()
    assert out.decode().split() == ["a", "b"]
    assert process.returncode == 0


async def test_stdin_reaches_the_process_and_its_close_ends_it(launcher: lc.Launcher, tmp_path: Path) -> None:
    process = await launcher.spawn(["cat"], cwd=str(tmp_path), env={"PATH": os.environ["PATH"]})
    payload = b"x" * (lc.CHUNK * 3 + 17)  # more than one frame
    process.stdin.write(payload)
    process.stdin.close()
    out, _ = await process.communicate()
    assert out == payload
    assert process.returncode == 0


async def test_a_command_that_cannot_start_says_so(launcher: lc.Launcher, tmp_path: Path) -> None:
    process = await launcher.spawn(["/nonexistent/binary"], cwd=str(tmp_path), env={})
    await process.communicate()
    assert process.returncode == lc.NOT_STARTED
    assert process.start_error


async def test_terminate_ends_the_whole_process_group(launcher: lc.Launcher, tmp_path: Path) -> None:
    marker = tmp_path / "pid"
    process = await launcher.spawn(
        f"sleep 60 & echo $! > {marker}; wait", cwd=str(tmp_path), env={"PATH": os.environ["PATH"]}
    )
    for _ in range(50):
        if marker.exists() and marker.read_text().strip():
            break
        await asyncio.sleep(0.1)
    child = int(marker.read_text())
    process.terminate()
    assert await asyncio.wait_for(process.wait(), 10) != 0
    await asyncio.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


async def test_a_runner_that_disconnects_takes_its_process_with_it(
    launcher: lc.Launcher, tmp_path: Path
) -> None:
    marker = tmp_path / "pid"
    process = await launcher.spawn(
        f"echo $$ > {marker}; exec sleep 60", cwd=str(tmp_path), env={"PATH": os.environ["PATH"]}
    )
    for _ in range(50):
        if marker.exists() and marker.read_text().strip():
            break
        await asyncio.sleep(0.1)
    pid = int(marker.read_text())
    await process.aclose()  # the runner is gone
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.1)
    pytest.fail("the process outlived the connection")


async def test_the_launcher_serves_with_the_group_writable_umask(tmp_path: Path) -> None:
    """What the agent writes in the shared volume must be writable by the runner's group."""
    port = free_port()
    old = os.umask(0o022)
    task = asyncio.create_task(lc.serve("127.0.0.1", port))
    try:
        launcher = lc.Launcher("127.0.0.1", port)
        assert await launcher.wait_ready(10)
        process = await launcher.spawn("umask", cwd=str(tmp_path), env={"PATH": os.environ["PATH"]})
        out, _ = await process.communicate()
        assert out.decode().strip() == "0002"
    finally:
        task.cancel()
        os.umask(old)


async def test_no_launcher_is_said_not_hidden() -> None:
    launcher = lc.Launcher("127.0.0.1", free_port())
    assert await launcher.wait_ready(0.2) is False
    with pytest.raises(lc.LauncherError):
        await launcher.spawn(["true"], cwd="/", env={})


async def test_a_peer_that_does_not_speak_the_protocol_is_dropped(launcher: lc.Launcher) -> None:
    reader, writer = await asyncio.open_connection(launcher.host, launcher.port)
    writer.write(b"GET / HTTP/1.1\r\n\r\n")
    await writer.drain()
    assert await asyncio.wait_for(reader.read(), 5) == b""  # closed, nothing started
    writer.close()


def test_the_file_runs_alone_with_the_standard_library_only() -> None:
    import ast

    tree = ast.parse(Path(lc.__file__).read_text(encoding="utf-8"))
    imported = {
        (node.module or "").split(".")[0] if isinstance(node, ast.ImportFrom) else alias.name.split(".")[0]
        for node in ast.walk(tree)
        for alias in (node.names if isinstance(node, ast.Import | ast.ImportFrom) else [])
    }
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported

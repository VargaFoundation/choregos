# SPDX-License-Identifier: Apache-2.0
"""The launcher: how the runner starts a process in the agent's container (ADR 0146 §2-3).

When the agent does not share the runner's container, the runner cannot start it as its child. The
agent's container runs `choregos-runner launcher`, which listens on the pod's loopback and starts
what the runner asks for: the coding agent, and the project's checks (which run the code the agent
wrote). One TCP connection carries one process: its command, environment and working directory in,
its three standard streams and its exit code over the same connection.

**Standard library only**, as `confine.py` is: this file is the whole of what runs in the agent's
container on the platform's behalf, and it can be read in one sitting. It is also copied alone into
the cluster test that proves the boundary (`tests/cluster/test_agent_isolation.py`).

**Nothing authenticates the launcher, on purpose.** It listens on loopback, in the agent's
container, which is untrusted whoever speaks to it: a process of that container that connects
learns nothing it did not own and starts a process it could start anyway. The trust runs the other
way: the runner believes what the launcher reports as far as that container is believed, and no
further.

Frames are `type (1 byte) | length (4 bytes, big endian) | payload`:

    client -> launcher   S start (JSON: command, shell, env, cwd)   I stdin   C close stdin
                         T terminate (SIGTERM to the process group)  K kill (SIGKILL)
    launcher -> client   O stdout   R stderr   X exit code (decimal)   F could not start (message)

A client that disconnects takes its process group with it: a runner that dies leaves nothing running.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import struct
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HEADER = struct.Struct(">cI")
#: What one frame may carry. The launcher cuts its own reads at `CHUNK`; a larger length is a peer
#: that is not speaking this protocol (or a hostile one), and the connection ends.
MAX_FRAME = 4 * 1024 * 1024
CHUNK = 64 * 1024
DEFAULT_PORT = 8766
DEFAULT_HOST = "127.0.0.1"
#: A launcher's exit code for a process it could not start (the shell's `command not found`).
NOT_STARTED = 127
#: What the client reports when the connection ends before the launcher said how the process did.
LOST = 255
#: The group bit the runner needs on what the agent writes (ADR 0146 §4): the two containers run as
#: different users and share one volume through the pod's `fsGroup`.
SHARED_UMASK = 0o002


def share_with_group(path: Path) -> bool:
    """Gives the group the working tree's root, setgid, so that the agent's container (another uid, the
    runner's group) can write in it (ADR 0146). A pod's `emptyDir` is so already (`fsGroup`); a Docker
    volume filled from the image's `/workspace` is the runner's alone. A root the caller does not own is
    left as it is: False."""
    try:
        mode = path.stat().st_mode & 0o7777
        os.chmod(path, mode | 0o2070)  # noqa: S103 - the group is the pod's two containers' only
    except OSError:
        return False
    return True


class LauncherError(RuntimeError):
    """The launcher is unreachable, or spoke something else than this protocol."""


def frame(kind: bytes, payload: bytes = b"") -> bytes:
    return HEADER.pack(kind, len(payload)) + payload


async def read_frame(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    """One frame, or `(b"", b"")` at the peer's end of stream. Raises `LauncherError` on a frame
    this protocol does not allow."""
    try:
        head = await reader.readexactly(HEADER.size)
    except asyncio.IncompleteReadError:
        return b"", b""
    kind, length = HEADER.unpack(head)
    if length > MAX_FRAME:
        raise LauncherError(f"a frame of {length} bytes (the most is {MAX_FRAME})")
    try:
        return kind, await reader.readexactly(length) if length else b""
    except asyncio.IncompleteReadError:
        return b"", b""


# ───────────────────────────── server: in the agent's container ─────────────────────────────


async def _pump(stream: asyncio.StreamReader | None, kind: bytes, writer: asyncio.StreamWriter) -> None:
    if stream is None:
        return
    while chunk := await stream.read(CHUNK):
        writer.write(frame(kind, chunk))
        await writer.drain()


async def _feed(reader: asyncio.StreamReader, process: asyncio.subprocess.Process) -> None:
    """Client frames after the start: stdin, its close, the signals. The end of the connection is
    the end of the process group."""
    stdin = process.stdin
    try:
        while True:
            kind, payload = await read_frame(reader)
            if kind == b"I" and stdin is not None:
                stdin.write(payload)
                await stdin.drain()
            elif kind == b"C" and stdin is not None:
                stdin.close()
            elif kind in (b"T", b"K"):
                _signal_group(process, signal.SIGTERM if kind == b"T" else signal.SIGKILL)
            elif kind == b"":
                break
    except (LauncherError, ConnectionError, OSError):
        pass
    _signal_group(process, signal.SIGKILL)


def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, sig)


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """One connection, one process."""
    try:
        kind, payload = await read_frame(reader)
        if kind != b"S":
            return
        request = json.loads(payload)
        command = request["command"]
        argv = ["/bin/sh", "-c", command] if request.get("shell") else list(command)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=request.get("cwd") or None,
                env=request.get("env"),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=CHUNK,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            writer.write(frame(b"F", str(exc).encode()))
            writer.write(frame(b"X", str(NOT_STARTED).encode()))
            await writer.drain()
            return
        feeder = asyncio.create_task(_feed(reader, process))
        try:
            await asyncio.gather(_pump(process.stdout, b"O", writer), _pump(process.stderr, b"R", writer))
            code = await process.wait()
            writer.write(frame(b"X", str(code).encode()))
            await writer.drain()
        finally:
            feeder.cancel()
            _signal_group(process, signal.SIGKILL)
    except (KeyError, TypeError, ValueError, LauncherError, ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Listens until SIGTERM or SIGINT. Holds nothing and starts nothing before a request: a warm
    sandbox's agent container is idle (ADR 0146 §2)."""
    os.umask(SHARED_UMASK)
    server = await asyncio.start_server(handle, host, port)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    print(f"choregos-launcher: listening on {host}:{port}", file=sys.stderr, flush=True)
    async with server:
        await stop.wait()


# ───────────────────────────── client: in the runner's container ─────────────────────────────


class _Pause:
    """What `StreamReader` calls to throttle its transport: here the connection's own reader. A
    process that writes faster than the runner reads makes the launcher's connection wait, as a pipe
    does, instead of filling the runner's memory (which is now its own cgroup)."""

    def __init__(self) -> None:
        self.open = asyncio.Event()
        self.open.set()

    def pause_reading(self) -> None:
        self.open.clear()

    def resume_reading(self) -> None:
        self.open.set()

    def is_closing(self) -> bool:
        return False


class _Stdin:
    def __init__(self, process: RemoteProcess) -> None:
        self._process = process

    def write(self, data: bytes) -> None:
        for start in range(0, len(data), CHUNK):
            self._process.send(frame(b"I", data[start : start + CHUNK]))

    async def drain(self) -> None:
        await self._process.drain()

    def close(self) -> None:
        self._process.send(frame(b"C"))


class RemoteProcess:
    """A process of the agent's container, shaped like `asyncio.subprocess.Process` for the parts
    the runner uses: `stdin`, `stdout`, `stderr`, `returncode`, `wait`, `terminate`, `kill`."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, limit: int = 2**16
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._flow = {"O": _Pause(), "R": _Pause()}
        self.stdout = asyncio.StreamReader(limit=limit)
        self.stderr = asyncio.StreamReader(limit=limit)
        self.stdout.set_transport(self._flow["O"])  # type: ignore[arg-type]
        self.stderr.set_transport(self._flow["R"])  # type: ignore[arg-type]
        self.stdin = _Stdin(self)
        self.returncode: int | None = None
        #: Why the process did not start, when the launcher said so.
        self.start_error: str | None = None
        self._done = asyncio.Event()
        self._task = asyncio.create_task(self._pump())

    def send(self, data: bytes) -> None:
        if not self._writer.is_closing():
            self._writer.write(data)

    async def drain(self) -> None:
        with contextlib.suppress(ConnectionError, OSError):
            await self._writer.drain()

    async def _pump(self) -> None:
        streams = {b"O": (self.stdout, self._flow["O"]), b"R": (self.stderr, self._flow["R"])}
        try:
            while True:
                kind, payload = await read_frame(self._reader)
                if kind in streams:
                    stream, flow = streams[kind]
                    stream.feed_data(payload)
                    await flow.open.wait()
                elif kind == b"X":
                    self.returncode = int(payload or LOST)
                    break
                elif kind == b"F":
                    self.start_error = payload.decode(errors="replace")
                elif kind == b"":
                    break
        except (LauncherError, ConnectionError, OSError, ValueError):
            pass
        if self.returncode is None:
            self.returncode = LOST
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._done.set()
        with contextlib.suppress(Exception):
            self._writer.close()

    async def wait(self) -> int:
        await self._done.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.send(frame(b"T"))

    def kill(self) -> None:
        self.send(frame(b"K"))

    async def communicate(self) -> tuple[bytes, bytes]:
        """Everything the process writes, then its exit."""
        out, err = await asyncio.gather(self.stdout.read(-1), self.stderr.read(-1))
        await self.wait()
        return out, err

    async def aclose(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        with contextlib.suppress(Exception):
            self._writer.close()


@dataclass(frozen=True, slots=True)
class Launcher:
    """The address of the agent container's launcher, and the verbs the runner uses."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    async def wait_ready(self, timeout: float = 60.0) -> bool:
        """True once the launcher accepts a connection; False after `timeout` seconds."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            try:
                _, writer = await asyncio.open_connection(self.host, self.port)
            except OSError:
                if loop.time() >= deadline:
                    return False
                await asyncio.sleep(0.5)
                continue
            writer.close()
            return True

    async def spawn(
        self,
        command: Sequence[str] | str,
        *,
        cwd: str,
        env: Mapping[str, str],
        limit: int = 2**16,
    ) -> RemoteProcess:
        """Starts `command` in the agent's container. A string is run by a shell."""
        try:
            reader, writer = await asyncio.open_connection(self.host, self.port, limit=2**20)
        except OSError as exc:
            raise LauncherError(f"no launcher at {self.host}:{self.port}: {exc}") from exc
        request: dict[str, Any] = {
            "command": command if isinstance(command, str) else list(command),
            "shell": isinstance(command, str),
            "cwd": cwd,
            "env": dict(env),
        }
        writer.write(frame(b"S", json.dumps(request).encode()))
        await writer.drain()
        return RemoteProcess(reader, writer, limit=limit)


if __name__ == "__main__":  # `python launcher.py [port]`: the file runs alone, as in the cluster test
    asyncio.run(serve(port=int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT))

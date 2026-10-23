"""`serve-session` relays the caller's socket to a REAL container's `/invocations_ws` (S41-21, S41-23).

`test_serve_session_socket.py` proves the relay against `FakeHostedAgent`. Here the container is a
real one (`fixtures/ws_agent.py` in `python:3.12-slim` with `websockets` pinned as the repository's
lock pins it), started by Docker with its port published to the loopback, and the runner side is the
real `SessionServer` with the real `WebsocketsConnector` and real HTTP for the readiness: the leg
between the runner and the container is a real TCP connection and a real WebSocket handshake.

What is not real: the other leg, the API that numbers and keeps the frames (`FakeSocketApi`, as in
the fake-container tests), and the shared network namespace of a pod (the container is reached at
the loopback through a published port instead of sharing the runner's namespace).

Marked `hosted_docker`: skipped unless `CHOREGOS_TEST_HOSTED_DOCKER=1` and a Docker daemon;
the `hosted-docker` CI job fails on a skip.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from choregos_adapters.fakes import FakeHostedAgent
from choregos_contracts import StageInput
from choregos_runner.exits import Exit

from .conftest import free_port
from .test_serve_session import _server, _session_input
from .test_serve_session_socket import FakeSocketApi, _frames

pytestmark = [pytest.mark.hosted_docker, pytest.mark.asyncio]

AGENT = Path(__file__).parent / "fixtures" / "ws_agent.py"
IMAGE = "s41-23-ws-agent"
#: The version `uv.lock` pins; `test_the_container_runs_the_pinned_websockets` keeps them equal.
WEBSOCKETS = "17.1"
DOCKERFILE = f"""FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir websockets=={WEBSOCKETS}
RUN useradd --uid 1000 --create-home agent
COPY ws_agent.py /app/ws_agent.py
USER 1000
CMD ["python", "/app/ws_agent.py"]
"""


def docker(*args: str, check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=600, input=stdin
    )


@pytest.fixture(scope="module")
def image() -> str:
    if os.environ.get("CHOREGOS_TEST_HOSTED_DOCKER") != "1":
        pytest.skip("CHOREGOS_TEST_HOSTED_DOCKER=1 is not set: this test starts containers")
    if shutil.which("docker") is None or docker("version", check=False).returncode != 0:
        pytest.skip("no reachable Docker daemon")
    # The Dockerfile comes on stdin, the agent from the fixtures folder as the build context.
    docker("build", "--quiet", "--tag", IMAGE, "--file", "-", str(AGENT.parent), stdin=DOCKERFILE)
    return IMAGE


@contextmanager
def running(image: str) -> Iterator[tuple[str, int]]:
    port, name = free_port(), "s41-23-ws-" + secrets.token_hex(3)
    docker(
        "run", "--detach", "--name", name, "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--env", f"PORT={port}", "--publish", f"127.0.0.1:{port}:{port}", image,
    )  # fmt: skip
    try:
        yield name, port
    finally:
        docker("rm", "--force", name, check=False)


async def relay(api: FakeSocketApi, settings: Any) -> Exit:
    server = _server(api, FakeHostedAgent(), settings)
    server.hosted_transport = None  # real HTTP for the readiness; the connector stays the real one
    server.socket_poll_s = 0
    server.readiness_interval = 0.2
    return await asyncio.wait_for(server.serve(), 60)


async def logs_with(name: str, text: str) -> str:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        out = docker("logs", name, check=False)
        if text in out.stdout + out.stderr:
            return out.stdout + out.stderr
        await asyncio.sleep(0.2)
    raise AssertionError(f"`{text}` never appeared in the container's logs")


async def test_frames_cross_a_real_websocket_numbered_and_the_containers_close_comes_back(
    stage_input: StageInput, runner_settings: Any, image: str
) -> None:
    with running(image) as (_, port):
        api = FakeSocketApi(_session_input(stage_input, socket=True, port=port), frames=_frames("hi", "bye"))
        assert await relay(api, runner_settings) is Exit.OK
    assert api.out == {
        1: "hello from the container",
        2: json.dumps({"type": "echo", "text": "hi"}),
        3: json.dumps({"type": "echo", "text": "bye"}),
    }, "the container's frames, numbered in the order it sent them"
    assert api.closed == {"code": 4000, "reason": "done"}, "the container's own close status"


async def test_the_callers_close_reaches_the_real_container(
    stage_input: StageInput, runner_settings: Any, image: str
) -> None:
    with running(image) as (name, port):
        api = FakeSocketApi(
            _session_input(stage_input, socket=True, port=port), frames=_frames("hi", close=True)
        )
        assert await relay(api, runner_settings) is Exit.OK
        logs = await logs_with(name, "peer closed")
    assert "peer closed 1000" in logs, "the relay closed the container's socket with the caller's code"


async def test_the_container_runs_the_pinned_websockets() -> None:
    lock = (Path(__file__).parents[3] / "uv.lock").read_text()
    assert f'name = "websockets"\nversion = "{WEBSOCKETS}"' in lock

# SPDX-License-Identifier: Apache-2.0
"""The agent's process is confined by the kernel before it starts (ADR 0094).

The kernel cases run the real wrapper against the running kernel and are skipped where Landlock is
absent; each one fails if the wrapper stops confining (a write outside the roots, a port outside
the list, the parent's environment read)."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import StageInput
from choregos_runner import confine
from choregos_runner.confine import Confinement, assess, connect_ports, wrap
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner
from choregos_runner.workspace import agent_home

from .conftest import FakeInternalClient, agent_script, valid_result

ABI = confine.landlock_abi()
needs_landlock = pytest.mark.skipif(ABI < 1, reason="no Landlock in this kernel")
needs_net = pytest.mark.skipif(ABI < confine.NET_ABI, reason="Landlock without TCP rules in this kernel")

PROXY_ENV = {
    "HTTPS_PROXY": "http://run:tok@choregos-egress.choregos.svc:3128",
    "NO_PROXY": "localhost,127.0.0.1,.svc,.cluster.local,10.0.0.0/8",
}


# ───────────────────────── policy, without the kernel ─────────────────────────


def test_off_holds_nothing_and_launches_the_command_as_is() -> None:
    confinement = Confinement("off", ("/w",), (3128,))
    assert wrap(["agent", "--acp"], confinement) == ["agent", "--acp"]
    report = assess(confinement, 7)
    assert (report.files, report.network, report.refused) == (False, False, False)


def test_best_effort_on_a_kernel_without_landlock_says_what_is_missing_and_launches() -> None:
    report = assess(Confinement("best_effort", ("/w",), (3128,)), 0)
    assert (report.files, report.network) == (False, False)
    assert report.missing == ("landlock", "landlock abi 4 (tcp connect)")
    assert not report.refused


def test_enforce_on_a_kernel_without_landlock_refuses() -> None:
    assert assess(Confinement("enforce", ("/w",)), 0).refused


def test_enforce_without_tcp_rules_refuses_only_when_ports_are_asked() -> None:
    assert assess(Confinement("enforce", ("/w",), (3128,)), 3).refused
    report = assess(Confinement("enforce", ("/w",), None), 3)
    assert report.files and not report.refused


def test_no_proxy_means_no_port_rule() -> None:
    assert connect_ports({}, ["http://choregos-api.choregos.svc:8000"]) is None


def test_with_a_proxy_only_its_port_and_the_direct_endpoints_ports_are_open() -> None:
    endpoints = [
        "http://choregos-api.choregos.svc:8000/healthz",
        "http://litellm.choregos.svc.cluster.local:4000",
        "http://127.0.0.1:7777/mcp",
        "http://10.2.3.4:8432",
        # Through the proxy: no port of its own, and 443 stays closed to a direct connection.
        "https://api.anthropic.com",
        "https://github.com/acme/app.git",
    ]
    assert connect_ports(PROXY_ENV, endpoints) == (3128, 4000, 7777, 8000, 8432)


def test_endpoint_urls_reads_the_platform_s_variables_only() -> None:
    env = {"ANTHROPIC_BASE_URL": "http://litellm:4000", "OPENAI_API_BASE": "http://x:1", "PATH": "/bin"}
    assert sorted(confine.endpoint_urls(env)) == ["http://litellm:4000", "http://x:1"]


def test_the_policy_survives_the_command_line() -> None:
    confinement = Confinement("enforce", ("/w", "/tmp"), (3128, 4000))
    assert Confinement.from_json(confinement.to_json()) == confinement
    assert Confinement.from_json(Confinement("best_effort").to_json()).connect_ports is None
    with pytest.raises(ValueError, match="unknown confinement mode"):
        Confinement.from_json('{"mode": "lax"}')


# ───────────────────────── the real kernel ─────────────────────────


def _confined(confinement: Confinement, code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        wrap([sys.executable, "-c", code], confinement),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_the_wrapper_adds_nothing_to_the_agents_stderr() -> None:
    # `python -m choregos_runner.confine` printed a runpy RuntimeWarning at every launch (dev, 2026-10-28).
    done = _confined(Confinement("best_effort", (tempfile.gettempdir(),)), "print('ok')")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"
    assert "Warning" not in done.stderr, done.stderr


@pytest.fixture
def outside() -> Iterator[Path]:
    """A directory outside the temporary directory, where a confined process may not write."""
    base = Path.cwd()
    if str(base).startswith(tempfile.gettempdir()):
        pytest.skip("the working directory is under the temporary directory")
    path = Path(tempfile.mkdtemp(prefix=".confinement-", dir=base))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@needs_landlock
def test_a_write_outside_the_roots_is_refused_by_the_kernel(outside: Path, tmp_path: Path) -> None:
    code = (
        "import sys\n"
        f"open({str(tmp_path / 'in.txt')!r}, 'w').write('ok')\n"
        f"try:\n    open({str(outside / 'out.txt')!r}, 'w').write('no')\n"
        "except PermissionError:\n    sys.exit(3)\n"
    )
    done = _confined(Confinement("enforce", (str(tmp_path),)), code)
    assert done.returncode == 3, done.stderr
    assert (tmp_path / "in.txt").read_text() == "ok"
    assert not (outside / "out.txt").exists()
    # Without the wrapper the same write goes through: the refusal is the kernel's, nobody else's.
    assert subprocess.run([sys.executable, "-c", code], timeout=60, check=False).returncode == 0


@pytest.fixture
def listeners() -> Iterator[tuple[int, int]]:
    """Two loopback ports that accept connections: one the policy opens, one it does not."""
    servers = [socket.create_server(("127.0.0.1", 0)) for _ in range(2)]

    def accept(server: socket.socket) -> None:
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            connection.close()

    for server in servers:
        threading.Thread(target=accept, args=(server,), daemon=True).start()
    try:
        yield servers[0].getsockname()[1], servers[1].getsockname()[1]
    finally:
        for server in servers:
            server.close()


@needs_net
def test_a_connection_to_a_port_outside_the_list_is_refused_by_the_kernel(
    listeners: tuple[int, int], tmp_path: Path
) -> None:
    allowed, other = listeners
    code = (
        "import socket, sys\n"
        f"socket.create_connection(('127.0.0.1', {allowed}), timeout=5).close()\n"
        f"try:\n    socket.create_connection(('127.0.0.1', {other}), timeout=5).close()\n"
        "except PermissionError:\n    sys.exit(3)\n"
    )
    done = _confined(Confinement("enforce", (str(tmp_path),), (allowed,)), code)
    assert done.returncode == 3, done.stderr
    assert subprocess.run([sys.executable, "-c", code], timeout=60, check=False).returncode == 0


@needs_landlock
def test_the_agent_cannot_read_the_runner_s_environment(tmp_path: Path) -> None:
    code = (
        "import os, sys\n"
        "try:\n    open(f'/proc/{os.getppid()}/environ', 'rb').read()\n"
        "except PermissionError:\n    sys.exit(3)\n"
    )
    # The wrapper `exec`s the command: its parent is the process that started the wrapper.
    agent = wrap([sys.executable, "-c", code], Confinement("enforce", (str(tmp_path),)))
    runner = subprocess.Popen(
        [sys.executable, "-c", f"import subprocess, sys; sys.exit(subprocess.run({agent!r}).returncode)"],
        env={**os.environ, "CHOREGOS_SECRET_FOR_TEST": "s3cret"},
    )
    assert runner.wait(timeout=60) == 3


def test_enforce_refuses_to_start_the_command_when_the_kernel_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(confine, "landlock_abi", lambda: 0)
    started = tmp_path / "started"
    policy = Confinement("enforce", (str(tmp_path),)).to_json()
    code = confine.main(["--policy", policy, "--", "touch", str(started)])
    assert code == confine.REFUSED
    assert not started.exists()
    assert "confinement refused: landlock missing" in capsys.readouterr().err


# ───────────────────────── the runner ─────────────────────────


@pytest.fixture
def own_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent's `$HOME` is `<tmp>/choregos-<run id>`, a fixed path: on a machine shared with
    another user it may already be theirs. These runs get a temporary directory of their own, and
    the confinement's write root follows it."""
    own = tmp_path / "tmp"
    own.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(own))


async def _run(
    stage_input: StageInput, runner_settings: Any, turn: dict[str, Any]
) -> tuple[Any, FakeInternalClient]:
    agent_script({"turns": [{**turn, "result": valid_result()}]}, stage_input, runner_settings.workspace)
    client = FakeInternalClient(stage_input)
    runner = Runner(runner_settings, client)  # type: ignore[arg-type]
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client


def _events(client: FakeInternalClient, kind: str) -> list[dict[str, Any]]:
    return [event["payload"] for event in client.events if event["type"] == kind]


@pytest.mark.usefixtures("own_tmp")
async def test_enforce_without_landlock_does_not_start_the_agent(
    stage_input: StageInput, runner_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(confine, "landlock_abi", lambda: 0)
    runner_settings.confinement = "enforce"
    outcome, client = await _run(stage_input, runner_settings, {})
    assert outcome.exit_code is Exit.CONFINEMENT_UNAVAILABLE
    [event] = _events(client, "agent.confinement")
    assert event["mode"] == "enforce" and event["missing"] == ["landlock"]
    assert [e["phase"] for e in _events(client, "run.failed")] == ["confinement"]
    assert client.results == []


@needs_net
@pytest.mark.usefixtures("own_tmp")
async def test_the_runner_confines_what_the_agent_does_without_asking(
    stage_input: StageInput,
    runner_settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    outside: Path,
    listeners: tuple[int, int],
) -> None:
    proxy, other = listeners
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{proxy}")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    unasked = [{"write": str(outside / "runner-code.py")}, {"connect": proxy}, {"connect": other}]
    outcome, client = await _run(stage_input, runner_settings, {"unasked": unasked})
    assert outcome.exit_code is Exit.OK, outcome.detail
    [event] = _events(client, "agent.confinement")
    assert event["mode"] == "best_effort" and event["files"] and event["network"], event
    assert proxy in event["connect_ports"] and other not in event["connect_ports"]
    outcomes = json.loads((agent_home(stage_input) / "unasked.json").read_text())
    assert outcomes == ["PermissionError", "ok", "PermissionError"]
    assert not (outside / "runner-code.py").exists()


@pytest.mark.usefixtures("own_tmp")
async def test_off_launches_the_agent_unconfined(
    stage_input: StageInput, runner_settings: Any, outside: Path
) -> None:
    runner_settings.confinement = "off"
    outcome, client = await _run(stage_input, runner_settings, {"unasked": [{"write": str(outside / "x")}]})
    assert outcome.exit_code is Exit.OK, outcome.detail
    assert _events(client, "agent.confinement")[0]["mode"] == "off"
    assert json.loads((agent_home(stage_input) / "unasked.json").read_text()) == ["ok"]

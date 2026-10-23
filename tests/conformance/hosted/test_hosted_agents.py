"""The conformance of hosted agents (ADR 0080): the reference passes, each way of breaking it fails.

The agents under test come from `CHOREGOS_CONFORMANCE_HOSTED_AGENTS`, JSON `{name: spec}` where a
spec is an argv, or `{"command": argv, "cwd": dir}`, or `{"image": ref}` (run through `docker run`),
with optional `protocol`, `readiness_timeout_s`, `termination_grace_s` and `env`. Without it, the
reference examples run (the Node one where `node` is installed). `make conformance-hosted
IMAGE=<ref>` (or `AGENT=<example directory>`, `PYTHON=<interpreter>`, `NODE=<node>`) sets it for one
agent.
"""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from .harness import (
    HOSTED_CHECKS,
    ROOT,
    HostedAgentSpec,
    container_command,
    free_port,
    needs_relay,
    node_example,
    python_example,
    relay_command,
    run_hosted_suite,
)

pytestmark = pytest.mark.conformance

EXAMPLES = ROOT / "examples" / "hosted"
ROGUE = Path(__file__).with_name("rogue_hosted_agent.py")


def configured_agents() -> dict[str, HostedAgentSpec]:
    raw = os.environ.get("CHOREGOS_CONFORMANCE_HOSTED_AGENTS")
    if not raw:
        return {"reference": python_example("reference"), "reference-node": node_example("reference-node")}
    return {name: HostedAgentSpec.parse(spec) for name, spec in json.loads(raw).items()}


@pytest.mark.parametrize("name", sorted(configured_agents()))
async def test_a_hosted_agent_passes_every_check(name: str) -> None:
    spec = configured_agents()[name]
    if spec.command and spec.command[0] == "node" and shutil.which("node") is None:
        pytest.skip("no `node` on this machine: the Node reference runs where Node is installed")
    timeout = float(os.environ.get("CHOREGOS_CONFORMANCE_HOSTED_TIMEOUT", "120"))
    result = await run_hosted_suite(name, spec, timeout_s=timeout)
    assert result.ok, result.failed
    assert set(result.passed) == set(HOSTED_CHECKS)


@pytest.mark.parametrize(
    ("rogue", "fails"),
    [
        ("never_ready", "readiness"),
        ("direct_model", "model_at_loopback"),
        ("no_tools", "mcp_at_loopback"),
        ("stray_egress", "no_egress"),
        ("ignores_sigterm", "sigterm"),
        ("retries_forever", "max_turns"),
    ],
)
async def test_an_agent_that_breaks_the_contract_fails_its_check(rogue: str, fails: str) -> None:
    """The suite must be able to fail, and on the right check: each rogue breaks one thing."""
    spec = HostedAgentSpec(
        command=[sys.executable, str(ROGUE)],
        readiness_timeout_s=2,
        termination_grace_s=2,
        env={"ROGUE": rogue},
    )
    result = await run_hosted_suite(f"rogue-{rogue}", spec, timeout_s=8)
    assert fails in result.failed, result.to_dict()
    if rogue not in ("never_ready", "no_tools"):
        # One broken thing breaks one check: the others still pass.
        assert set(result.failed) == {fails}, result.failed


def test_every_example_carries_the_same_server() -> None:
    """No shared library (ADR 0063): each example copies `responses_server.py`, and no copy drifts."""
    reference = EXAMPLES / "reference" / "responses_server.py"
    copies = sorted(EXAMPLES.glob("*/responses_server.py"))
    assert len(copies) >= 2, copies
    drifted = [str(c.relative_to(ROOT)) for c in copies if not filecmp.cmp(c, reference, shallow=False)]
    assert not drifted, f"copies that differ from the reference: {drifted}"


def test_every_node_example_carries_the_same_server() -> None:
    """The same for Node: each Node example copies `responses_server.mjs` from the Node reference."""
    reference = EXAMPLES / "reference-node" / "responses_server.mjs"
    copies = sorted(EXAMPLES.glob("*/responses_server.mjs"))
    assert len(copies) >= 2, copies
    drifted = [str(c.relative_to(ROOT)) for c in copies if not filecmp.cmp(c, reference, shallow=False)]
    assert not drifted, f"copies that differ from the reference: {drifted}"


def test_the_node_server_exits_0_on_sigterm() -> None:
    """The `sigterm` check times the exit; a Node process killed by the signal exits as fast, so the
    handler that stops cleanly is checked here: exit code 0, not the signal's."""
    if shutil.which("node") is None:
        pytest.skip("no `node` on this machine")
    port = free_port()
    spec = node_example("reference-node")
    server = subprocess.Popen(spec.command, cwd=spec.cwd, env={**os.environ, "PORT": str(port)})
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/readiness", timeout=1).close()
                break
            except OSError:
                assert time.monotonic() < deadline, "the Node reference never got ready"
                time.sleep(0.1)
        server.send_signal(signal.SIGTERM)
        assert server.wait(timeout=10) == 0
    finally:
        server.kill()


def test_an_image_agent_runs_through_docker_with_the_environment_by_name() -> None:
    command = container_command("registry.example/a@sha256:" + "0" * 64, {"B": "1", "A": "2"}, "c-1")
    assert command[:6] == ["docker", "run", "--rm", "--name", "c-1", "--network=host"]
    assert "--cap-drop=ALL" in command
    assert command[-5:] == ["-e", "A", "-e", "B", "registry.example/a@sha256:" + "0" * 64], (
        "variables are passed by name, never by value"
    )


def test_under_docker_desktop_the_agent_joins_a_relay_that_forwards_the_loopback_listeners(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """S41-23: Docker Desktop's `--network=host` is the VM's, so the agent joins a socat container."""
    command = container_command("img@sha256:" + "0" * 64, {"A": "1"}, "c-1", network="container:c-1-net")
    assert "--network=container:c-1-net" in command and "--network=host" not in command
    relay = relay_command("c-1-net", [4000, 4001], 8088)
    assert relay[relay.index("-p") : relay.index("-p") + 2] == ["-p", "127.0.0.1:8088:8088"]
    script = relay[-1]
    assert "TCP-LISTEN:4000,bind=127.0.0.1" in script and "TCP:host.docker.internal:4001" in script
    monkeypatch.setenv("CHOREGOS_CONFORMANCE_DOCKER_NETWORK", "relay")
    assert needs_relay()
    monkeypatch.setenv("CHOREGOS_CONFORMANCE_DOCKER_NETWORK", "host")
    assert not needs_relay()

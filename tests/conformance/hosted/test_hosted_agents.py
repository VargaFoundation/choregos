"""The conformance of hosted agents (ADR 0080): the reference passes, each way of breaking it fails.

The agents under test come from `CHOREGOS_CONFORMANCE_HOSTED_AGENTS`, JSON `{name: spec}` where a
spec is an argv, or `{"command": argv, "cwd": dir}`, or `{"image": ref}` (run through `docker run`),
with optional `protocol`, `readiness_timeout_s`, `termination_grace_s` and `env`. Without it, the
reference example runs. `make conformance-hosted IMAGE=<ref>` (or `AGENT=<example directory>`,
`PYTHON=<interpreter>`) sets it for one agent.
"""

from __future__ import annotations

import filecmp
import json
import os
import sys
from pathlib import Path

import pytest

from .harness import (
    HOSTED_CHECKS,
    ROOT,
    HostedAgentSpec,
    container_command,
    python_example,
    run_hosted_suite,
)

pytestmark = pytest.mark.conformance

EXAMPLES = ROOT / "examples" / "hosted"
ROGUE = Path(__file__).with_name("rogue_hosted_agent.py")


def configured_agents() -> dict[str, HostedAgentSpec]:
    raw = os.environ.get("CHOREGOS_CONFORMANCE_HOSTED_AGENTS")
    if not raw:
        return {"reference": python_example("reference")}
    return {name: HostedAgentSpec.parse(spec) for name, spec in json.loads(raw).items()}


@pytest.mark.parametrize("name", sorted(configured_agents()))
async def test_a_hosted_agent_passes_every_check(name: str) -> None:
    spec = configured_agents()[name]
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


def test_an_image_agent_runs_through_docker_with_the_environment_by_name() -> None:
    command = container_command("registry.example/a@sha256:" + "0" * 64, {"B": "1", "A": "2"}, "c-1")
    assert command[:6] == ["docker", "run", "--rm", "--name", "c-1", "--network=host"]
    assert "--cap-drop=ALL" in command
    assert command[-5:] == ["-e", "A", "-e", "B", "registry.example/a@sha256:" + "0" * 64], (
        "variables are passed by name, never by value"
    )

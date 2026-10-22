"""The hosted conformance suite with the agent inside a gVisor sandbox (S41-17, ADR 0080).

The cluster puts a hosted pod under the `gvisor` RuntimeClass (the project's `require-gvisor`
policy refuses one without it). What that runtime can break is the agent's side of the contract:
a framework that needs a syscall gVisor does not implement, a server that never sees SIGTERM
through the sentry, a loopback the sandbox does not reach. So the same eight checks run with the
agent's process wrapped in `runsc do`: the reference examples pass them, and the rogues that break
SIGTERM or egress still fail theirs, so the sandbox does not hide a broken agent either.

`--network=host` keeps the runner's listeners where they are on a pod, on the loopback the two
containers share; `--rootless` and `--ignore-cgroups` let it run without root. Skipped without
`CHOREGOS_TEST_RUNSC`, the path to `runsc` (its `gvisor-bin/` beside it): the `hosted-gvisor` CI
job sets it and fails on a skip.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .harness import HOSTED_CHECKS, HostedAgentSpec, node_example, python_example, run_hosted_suite

pytestmark = pytest.mark.gvisor

RUNSC = os.environ.get("CHOREGOS_TEST_RUNSC", "")
ROGUE = Path(__file__).with_name("rogue_hosted_agent.py")
SANDBOX = ["--rootless", "--network=host", "--ignore-cgroups", "do"]


@pytest.fixture
def runsc() -> str:
    if not RUNSC:
        pytest.skip("CHOREGOS_TEST_RUNSC is not set: no gVisor on this machine")
    return RUNSC


def under_gvisor(runsc: str, spec: HostedAgentSpec) -> HostedAgentSpec:
    """The agent's command inside the sandbox, after a line that says which kernel it runs on: the
    agent's log then shows the suite ran it under gVisor, not beside it."""
    # One descriptor for both streams inside the sandbox: gVisor imports the host's 1 and 2 as two
    # files, each at its own offset, so on the suite's log file one stream overwrites the other.
    shell = ["sh", "-c", 'exec 2>&1; echo "kernel: $(uname -r)"; exec "$@"', "sh"]
    return dataclasses.replace(spec, command=[runsc, *SANDBOX, *shell, *spec.command])


def test_the_sandbox_is_gvisor(runsc: str) -> None:
    """Not a plain `exec`: the kernel the agent sees is the sentry's."""
    kernel = subprocess.run(
        [runsc, *SANDBOX, "uname", "-r"], capture_output=True, text=True, timeout=60, check=True
    ).stdout
    assert "gvisor" in kernel, kernel


@pytest.mark.parametrize("example", ["reference", "reference-node"])
async def test_a_hosted_agent_passes_every_check_under_gvisor(
    runsc: str, example: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))  # where the suite writes the agent's log
    if example.endswith("-node"):
        node = shutil.which("node")
        if node is None:
            pytest.skip("no `node` on this machine")
        spec = node_example(example, node)
    else:
        spec = python_example(example)
    result = await run_hosted_suite(f"gvisor-{example}", under_gvisor(runsc, spec), timeout_s=120)
    assert result.ok, result.failed
    assert set(result.passed) == set(HOSTED_CHECKS)
    logs = [log.read_text(errors="replace") for log in tmp_path.glob("choregos-conformance-*.log")]
    assert logs and all("-gvisor" in log for log in logs), logs


@pytest.mark.parametrize(("rogue", "fails"), [("ignores_sigterm", "sigterm"), ("stray_egress", "no_egress")])
async def test_an_agent_that_breaks_the_contract_still_fails_under_gvisor(
    runsc: str, rogue: str, fails: str
) -> None:
    spec = HostedAgentSpec(
        command=[sys.executable, str(ROGUE)],
        readiness_timeout_s=10,
        termination_grace_s=3,
        env={"ROGUE": rogue},
    )
    result = await run_hosted_suite(f"gvisor-rogue-{rogue}", under_gvisor(runsc, spec), timeout_s=30)
    assert set(result.failed) == {fails}, result.to_dict()

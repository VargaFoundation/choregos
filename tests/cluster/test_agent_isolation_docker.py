"""ADR 0146 (S65-04h) — `local_docker`'s agent container cannot read the runner's secrets, on a real Docker.

The containers, users, volumes and network are the ones `LocalDockerExecutor` starts under `container`:
the runner's (uid 1000, the run token in its environment) and the agent's (`--user 1001:1000`,
`--read-only`, the runner's network namespace, the tree and the exchange volumes). Only the image is
the test's: `python:3.12-slim` with `launcher.py` (the file the runner image ships) and the probe of
`test_agent_isolation.py` as its runner, dispatched on the first argument as the runner image's
entrypoint is. The runner gives the tree's root to its group with the runner's own function
(`launcher.share_with_group`), then asks the launcher for the probe and runs it as its own child.

Docker, not a cluster: it lives here because a capability's proof is a test of `tests/cluster` (S35-04),
and it runs with them (`make test-cluster`, which needs Docker for kind anyway).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from typing import Any

import pytest

from .test_agent_isolation import LAUNCHER, RUNNER, SECRET

pytestmark = pytest.mark.cluster

IMAGE = "choregos-test/agent-isolation-docker:local"
ENTRY = """
import runpy, sys
if sys.argv[1:2] == ["launcher"]:
    sys.argv = ["launcher", sys.argv[sys.argv.index("--port") + 1]]
    runpy.run_path("/cfg/launcher.py", run_name="__main__")
else:
    runpy.run_path("/cfg/runner.py", run_name="__main__")
"""
#: The runner script of the pod test, with the tree's root given to the group first, as the runner does.
DOCKER_RUNNER = RUNNER.replace(
    "async def main():\n",
    "async def main():\n    import pathlib\n    lc.share_with_group(pathlib.Path('/workspace'))\n",
)
DOCKERFILE = """
FROM python:3.12-slim
RUN useradd --uid 1000 --create-home --home-dir /workspace runner && chmod 0755 /workspace
COPY launcher.py runner.py entry.py /cfg/
USER 1000
WORKDIR /workspace
ENTRYPOINT ["python", "/cfg/entry.py"]
"""


@pytest.fixture(scope="module")
def image(tmp_path_factory: pytest.TempPathFactory) -> str:
    if not os.environ.get("TRANIBOR_CLUSTER_CONTEXT"):
        pytest.skip("run with the cluster tests (`make test-cluster`)")
    docker = shutil.which("docker")
    if docker is None or subprocess.run([docker, "info"], check=False, capture_output=True).returncode:
        pytest.skip("docker is needed")
    context = tmp_path_factory.mktemp("image")
    shutil.copy(LAUNCHER, context / "launcher.py")
    (context / "runner.py").write_text(DOCKER_RUNNER, encoding="utf-8")
    (context / "entry.py").write_text(ENTRY, encoding="utf-8")
    (context / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    built = subprocess.run(
        ["docker", "build", "-q", "-t", IMAGE, str(context)], check=False, capture_output=True, text=True
    )
    assert built.returncode == 0, built.stderr[-800:]
    return IMAGE


@pytest.fixture(scope="module")
def result(image: str) -> dict[str, Any]:
    from tranibor_adapters.executor.local_docker import LocalDockerExecutor
    from tranibor_core.domain import StageJobSpec

    class _Executor(LocalDockerExecutor):
        async def _prune_finished(self) -> None:
            """Never another run's containers on a shared Docker."""

    network = "choregos-isolation-test"
    subprocess.run(["docker", "network", "create", network], check=False, capture_output=True)
    executor = _Executor(network=network)
    spec = StageJobSpec(
        run_id="isolation-docker",
        project_slug="demo",
        namespace="local",
        runner_image=image,
        api_url="http://choregos-api:8000",
        run_token=SECRET,
        agent_isolation="container",
        runner_command=["runner-marker-8841"],
    )

    async def run() -> tuple[str, str]:
        ref = await executor.start(spec)
        try:
            for _ in range(120):
                status = await executor.status(ref)
                if status.finished:
                    break
                await asyncio.sleep(1)
            lines = [line async for line in executor.logs(ref)]
            return status.state, "\n".join(lines)
        finally:
            await executor.cancel(ref)
            volumes = [f"{ref.name}-tree", f"{ref.name}-exchange"]
            subprocess.run(["docker", "volume", "rm", "-f", *volumes], check=False, capture_output=True)

    state, logs = asyncio.run(run())
    subprocess.run(["docker", "network", "rm", network], check=False, capture_output=True)
    line = next((ln for ln in logs.splitlines() if ln.startswith("RESULT=")), "")
    assert state == "succeeded" and line, f"state={state}\n{logs[-1500:]}"
    return dict(json.loads(line[len("RESULT=") :]))


@pytest.mark.capability("local_docker", "separate_agent")
def test_docker_s_agent_container_reads_nothing_of_the_runner(result: dict[str, Any]) -> None:
    control = result["process"]
    assert control["uid"] == "1000" and int(control["env_hits"]) >= 1, "the probe can find what it seeks"
    container = result["container"]
    assert container["uid"] == "1001"
    assert container["env_hits"] == "0", result
    assert container["runner_processes"] == "0" and container["runner_file"] == "absent", result


@pytest.mark.capability("local_docker", "separate_agent")
def test_docker_s_two_volumes_carry_the_work_both_ways(result: dict[str, Any]) -> None:
    assert result["container"]["wrote"] == "ok", result
    assert result["shared"] == {
        "/workspace/from-agent": "agent",
        "/workspace/from-agent:append": "ok",
        "/exchange/from-agent": "agent",
        "/exchange/from-agent:append": "ok",
    }, result

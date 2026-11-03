"""The local Docker executor: the commands it launches, without a Docker daemon."""

from __future__ import annotations

import io
import json
import tarfile
from typing import Any

import pytest
from choregos_adapters.errors import UpstreamError
from choregos_adapters.executor.local_docker import LocalDockerExecutor
from choregos_core.domain import AgentContainer, ExecRef, StageJobSpec


class _Docker:
    """A simulated `docker`: `replies` per sub-command, recorded `calls`."""

    def __init__(self, **replies: tuple[int, str, str]) -> None:
        self.replies = replies
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []

    async def __call__(
        self, *args: str, timeout: float = 120.0, env: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        self.calls.append(list(args))
        self.envs.append(env)
        return self.replies.get(args[0], (0, "", ""))


def executor_for(docker: _Docker) -> LocalDockerExecutor:
    ex = LocalDockerExecutor(network="choregos_net")
    ex._run = docker  # type: ignore[method-assign]
    return ex


def spec() -> StageJobSpec:
    return StageJobSpec(
        run_id="R-1",
        project_slug="billing",
        namespace="local",
        runner_image="runner:dev",
        api_url="http://api:8000",
        run_token="jeton",
        env={"HTTPS_PROXY": "http://p:3128"},
    )


async def test_start_runs_the_container_with_the_same_variables_as_in_a_cluster() -> None:
    docker = _Docker(inspect=(1, "", "no such container"))
    ref = await executor_for(docker).start(spec())
    assert ref.name == "choregos-run-r-1"
    run = next(a for a in docker.calls if a[0] == "run")
    assert "--network" in run and run[run.index("--network") + 1] == "choregos_net"
    envs = [run[i + 1] for i, a in enumerate(run) if a == "--env"]
    assert "CHOREGOS_RUN_TOKEN" in envs and "HTTPS_PROXY=http://p:3128" in envs
    assert run[-2:] == ["runner:dev", "run"]


async def test_the_run_secrets_are_named_on_the_command_line_and_never_valued_there() -> None:
    """#439: `ps` on the host shows argv; the values travel in docker's own environment."""
    docker = _Docker(inspect=(1, "", "no such container"))
    secret = spec().model_copy(update={"gateway_key": "gw-secret", "remote_token": "remote-secret"})
    await executor_for(docker).start(secret)
    index = next(i for i, a in enumerate(docker.calls) if a[0] == "run")
    argv = " ".join(docker.calls[index])
    for value in ("jeton", "gw-secret", "remote-secret"):  # lang: fr-ok
        assert value not in argv
    env = docker.envs[index] or {}
    assert env["CHOREGOS_RUN_TOKEN"] == "jeton"  # lang: fr-ok
    assert set(env.values()) == {"jeton", "gw-secret", "remote-secret"}  # lang: fr-ok
    named = [docker.calls[index][i + 1] for i, a in enumerate(docker.calls[index]) if a == "--env"]
    assert set(env) <= set(named)


async def test_docker_receives_the_environment_it_is_given_on_top_of_its_own() -> None:
    """The real subprocess call: without `env=`, `--env NAME` would hand the container nothing."""
    ex = LocalDockerExecutor(docker="sh")
    script = 'echo "$CHOREGOS_RUN_TOKEN:${PATH:+path}"'
    code, stdout, _ = await ex._run("-c", script, env={"CHOREGOS_RUN_TOKEN": "t"})
    assert code == 0 and stdout.strip() == "t:path"


async def test_starting_a_run_removes_the_finished_containers_beyond_the_newest_ten() -> None:
    """#439: every run used to leave its container, and its workspace, behind for good."""
    names = "\n".join(f"choregos-run-{i}" for i in range(12))
    docker = _Docker(inspect=(1, "", "no such container"), ps=(0, names, ""))
    await executor_for(docker).start(spec())
    ps = next(a for a in docker.calls if a[0] == "ps")
    assert "label=choregos.run-id" in ps and "status=exited" in ps
    assert ["rm", "--force", "choregos-run-10", "choregos-run-11"] in docker.calls
    assert docker.calls[-1][0] == "run"


async def test_a_failed_listing_or_few_finished_containers_remove_nothing() -> None:
    for ps in ((1, "", "denied"), (0, "choregos-run-a\nchoregos-run-b", "")):
        docker = _Docker(inspect=(1, "", "no such container"), ps=ps)
        await executor_for(docker).start(spec())
        assert not [a for a in docker.calls if a[0] == "rm"]
        assert docker.calls[-1][0] == "run"


async def test_start_is_idempotent_if_the_container_exists() -> None:
    docker = _Docker(inspect=(0, "[]", ""))
    await executor_for(docker).start(spec())
    assert [a[0] for a in docker.calls] == ["inspect"]


async def test_a_start_failure_is_named() -> None:
    docker = _Docker(inspect=(1, "", ""), run=(125, "", "image not found"))
    with pytest.raises(UpstreamError, match="image not found"):
        await executor_for(docker).start(spec())


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"Running": True}, "running"),
        ({"Running": False, "ExitCode": 0}, "succeeded"),
        ({"Running": False, "ExitCode": 2, "Error": "boom"}, "failed"),
    ],
)
async def test_the_state_comes_from_docker_inspect(state: dict[str, Any], expected: str) -> None:
    docker = _Docker(inspect=(0, json.dumps([{"State": state}]), ""))
    exec_status = await executor_for(docker).status(ExecRef(kind="local_docker", name="c"))
    assert exec_status.state == expected
    if expected == "failed":
        assert exec_status.exit_code == 2 and exec_status.message == "boom"


def _tar(content: bytes) -> bytes:
    """What `docker cp <container>:<file> -` writes on stdout: a tar holding the file."""
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w") as archive:
        info = tarfile.TarInfo("result.json")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return tampon.getvalue()


def reader(code: int, output: bytes) -> tuple[LocalDockerExecutor, list[list[str]]]:
    ex = LocalDockerExecutor(network="choregos_net")
    calls: list[list[str]] = []

    async def raw(*args: str, timeout: float = 120.0) -> tuple[int, bytes, str]:
        calls.append(list(args))
        return code, output, ""

    ex._run_raw = raw  # type: ignore[method-assign]
    return ex, calls


async def test_the_result_is_read_from_the_stopped_container_and_bad_json_gives_none() -> None:
    """`docker exec` needs a running container; the result is read once the run has ENDED."""
    bon = json.dumps({"schema": "choregos/StageResult/v1", "status": "done", "summary": "ok"}).encode()
    ex, calls = reader(0, _tar(bon))
    result = await ex.fetch_result(ExecRef(kind="local_docker", name="c"))
    assert result is not None and result.status == "done"
    assert calls == [["cp", "c:/workspace/.choregos/result.json", "-"]]
    ex, _ = reader(0, _tar(b"{not json"))
    assert await ex.fetch_result(ExecRef(kind="local_docker", name="c")) is None
    ex, _ = reader(1, b"")
    assert await ex.fetch_result(ExecRef(kind="local_docker", name="c")) is None


async def test_the_run_container_drops_capabilities_and_waits_for_the_image_pull() -> None:
    docker = _Docker(inspect=(1, "", "no such container"))
    timeouts: list[float] = []

    async def enregistre(
        *args: str, timeout: float = 120.0, env: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        timeouts.append(timeout)
        return await docker(*args, timeout=timeout, env=env)

    ex = LocalDockerExecutor(network="choregos_net")
    ex._run = enregistre  # type: ignore[method-assign]
    await ex.start(spec())
    run = next(a for a in docker.calls if a[0] == "run")
    assert run[run.index("--cap-drop") + 1] == "ALL"
    assert run[run.index("--security-opt") + 1] == "no-new-privileges"
    assert max(timeouts) >= 600, "a first pull of the runner image takes minutes"


async def test_the_logs_and_the_cancellation() -> None:
    docker = _Docker(logs=(0, "a\nb\n", "c"))
    ex = executor_for(docker)
    assert [line async for line in ex.logs(ExecRef(kind="local_docker", name="c"))] == ["a", "b", "c"]
    await ex.cancel(ExecRef(kind="local_docker", name="c"))
    assert docker.calls[-1] == ["rm", "-f", "c", "c-agent"], "a hosted agent beside it goes too"


DIGEST = "sha256:" + "a" * 64


def hosted_spec() -> StageJobSpec:
    agent = AgentContainer(
        image=f"ghcr.io/acme/triager@{DIGEST}",
        env={"PORT": "8088", "OPENAI_API_KEY": "choregos-loopback"},
        termination_grace_s=12,
    )
    return spec().model_copy(update={"gateway_key": "gw-secret", "agent_container": agent})


async def test_a_hosted_agent_shares_the_runners_network_and_none_of_its_secrets() -> None:
    """ADR 0080: the sidecar reaches the runner's loopback listeners, and nothing else of it."""
    docker = _Docker(inspect=(1, "", "no such container"))
    await executor_for(docker).start(hosted_spec())
    runs = [(i, a) for i, a in enumerate(docker.calls) if a[0] == "run"]
    assert [a[-1] for _, a in runs] == ["run", f"ghcr.io/acme/triager@{DIGEST}"], "the runner first"
    index, agent = runs[1]
    assert agent[agent.index("--name") + 1] == "choregos-run-r-1-agent"
    assert agent[agent.index("--network") + 1] == "container:choregos-run-r-1"
    assert agent[agent.index("--cap-drop") + 1] == "ALL"
    assert agent[agent.index("--stop-timeout") + 1] == "12"
    envs = [agent[i + 1] for i, a in enumerate(agent) if a == "--env"]
    assert sorted(envs) == ["OPENAI_API_KEY=choregos-loopback", "PORT=8088"]
    assert not any(
        e.startswith(("CHOREGOS_RUN_TOKEN", "CHOREGOS_RUN_GATEWAY_KEY", "HTTPS_PROXY")) for e in envs
    )
    assert docker.envs[index] is None, "no secret travels in docker's environment for the agent"


async def test_an_agent_that_cannot_start_takes_the_runner_down_with_it() -> None:
    class _Failing(_Docker):
        async def __call__(
            self, *args: str, timeout: float = 120.0, env: dict[str, str] | None = None
        ) -> tuple[int, str, str]:
            await super().__call__(*args, timeout=timeout, env=env)
            if args[0] == "inspect":
                return 1, "", "no such container"
            if args[0] == "run" and args[-1].startswith("ghcr.io/"):
                return 125, "", "manifest unknown"
            return 0, "", ""

    docker = _Failing()
    with pytest.raises(UpstreamError, match="manifest unknown"):
        await executor_for(docker).start(hosted_spec())
    assert docker.calls[-1] == ["rm", "--force", "choregos-run-r-1"]


async def test_a_finished_runner_stops_the_agent_beside_it() -> None:
    done = json.dumps([{"State": {"Running": False, "ExitCode": 0}}])
    docker = _Docker(inspect=(0, done, ""))
    exec_status = await executor_for(docker).status(ExecRef(kind="local_docker", name="c"))
    assert exec_status.state == "succeeded"
    assert ["rm", "--force", "c-agent"] in docker.calls
    running = _Docker(inspect=(0, json.dumps([{"State": {"Running": True}}]), ""))
    await executor_for(running).status(ExecRef(kind="local_docker", name="c"))
    assert not [a for a in running.calls if a[0] == "rm"], "a running sidecar is left alone"


def _working(workspace: str) -> StageJobSpec:
    hosted = hosted_spec()
    assert hosted.agent_container is not None
    agent = hosted.agent_container.model_copy(update={"workspace": workspace})
    return hosted.model_copy(update={"agent_container": agent})


def _volumes(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == "--volume"]


@pytest.mark.parametrize(("workspace", "mount"), [("read", "/workspace:ro"), ("write", "/workspace")])
async def test_a_hosted_agent_that_asks_for_the_workspace_gets_the_runners_copy(
    workspace: str, mount: str
) -> None:
    """S41-18: one volume per run, at `/hosted-workspace` in the runner and `/workspace` in the
    agent, which joins the runner's group to change the files the runner copied."""
    docker = _Docker(inspect=(1, "", "no such container"))
    await executor_for(docker).start(_working(workspace))
    runner, agent = [a for a in docker.calls if a[0] == "run"]
    assert _volumes(runner) == ["choregos-run-r-1-workspace:/hosted-workspace"]
    assert _volumes(agent) == [f"choregos-run-r-1-workspace:{mount}"]
    assert agent[agent.index("--group-add") + 1] == "1000"


async def test_a_hosted_agent_without_the_workspace_gets_no_volume() -> None:
    docker = _Docker(inspect=(1, "", "no such container"))
    await executor_for(docker).start(_working("none"))
    runner, agent = [a for a in docker.calls if a[0] == "run"]
    assert _volumes(runner) == [] and _volumes(agent) == [] and "--group-add" not in agent


async def test_pruning_a_finished_run_removes_its_workspace_volume_too() -> None:
    names = "\n".join(f"choregos-run-{i}" for i in range(11))
    docker = _Docker(inspect=(1, "", "no such container"), ps=(0, names, ""))
    await executor_for(docker).start(spec())
    assert ["volume", "rm", "--force", "choregos-run-10-workspace"] in docker.calls

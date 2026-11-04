"""The agent never holds the run token, on any executor (ADR 0115, S57-02).

Every executor but `agent_sandbox` puts the run's Secret in the runner container's environment:
`CHOREGOS_RUN_TOKEN`, with the gateway key and the remote agent's token beside it. The runner reads
them into its settings. The agent it starts (`AcpClient`, the confinement's view of it, the `code`
backend's agent too), the project's checks that run the code the agent wrote, and the runner's git
get none of them; the agent gets its gateway key under its backend's own variable. A job still gets
its run token, by name (ADR 0056).

The environments are the executors' own, read from what each one builds: the Job's runner container
(`k8s_job`), the Container Apps job's definition (ACA), `docker run`'s variables (local Docker) and the
reference Tekton Task. The hosted container's environment is built from nothing (`sidecar_env`):
`packages/adapters/tests/test_hosted_executors.py` and `test_local_docker.py` hold that one.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_adapters.executor.aca import AcaExecutor, AzureArmClient
from choregos_adapters.executor.k8s_job import KubernetesJobExecutor
from choregos_adapters.executor.local_docker import LocalDockerExecutor
from choregos_adapters.executor.tekton import run_secret_data
from choregos_contracts import AgentRef, JobSpec, ModelRef, StageInput
from choregos_core.domain import StageJobSpec
from choregos_runner import confine
from choregos_runner import runner as runner_module
from choregos_runner.acp import AcpClient
from choregos_runner.config import RunnerSettings, agent_base_environment
from choregos_runner.dod import run_checks
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script, free_port, valid_result

ROOT = Path(__file__).resolve().parents[3]
TEKTON_TASK = ROOT / "templates" / "github-tekton-argo-k8s" / "tekton" / "agent-stage.yaml"

RUN_TOKEN = "s57-02-the-run-token"
REMOTE_TOKEN = "s57-02-the-remote-agent-token"
GATEWAY_KEY = "sk-s57-02-the-run-gateway-key"
#: The run's credentials as the executors name them.
RUN_SECRETS = {
    "CHOREGOS_RUN_TOKEN": RUN_TOKEN,
    "CHOREGOS_RUN_REMOTE_TOKEN": REMOTE_TOKEN,
    "CHOREGOS_RUN_GATEWAY_KEY": GATEWAY_KEY,
}


def _spec() -> StageJobSpec:
    return StageJobSpec(
        run_id="run-test-1",
        project_slug="demo",
        namespace="choregos",
        runner_image="runner:test",
        api_url="http://api.invalid/internal",
        run_token=RUN_TOKEN,
        gateway_key=GATEWAY_KEY,
        remote_token=REMOTE_TOKEN,
    )


# ───────────────────────── the runner's environment, as each executor sets it ─────────────────────────


def _kubernetes_env(entries: list[dict[str, Any]], spec: StageJobSpec) -> dict[str, str]:
    """A container's `env`, with each `secretKeyRef` read from the run's Secret, as the kubelet does."""
    data = run_secret_data(spec)
    env: dict[str, str] = {}
    for entry in entries:
        if "value" in entry:
            env[entry["name"]] = str(entry["value"])
        elif (key := entry["valueFrom"]["secretKeyRef"]["key"]) in data:
            env[entry["name"]] = base64.b64decode(data[key]).decode()
    return env


async def _k8s_job(spec: StageJobSpec) -> dict[str, str]:
    job = KubernetesJobExecutor(client=object())._job(spec, "run-test-1")  # type: ignore[arg-type]
    [runner] = job["spec"]["template"]["spec"]["containers"]
    return _kubernetes_env(runner["env"], spec)


async def _aca(spec: StageJobSpec) -> dict[str, str]:
    definition = AcaExecutor(AzureArmClient(token="arm"), environment_id="/env")._definition(spec, "run-x")
    secrets = {s["name"]: s["value"] for s in definition["properties"]["configuration"]["secrets"]}
    [container] = definition["properties"]["template"]["containers"]
    return {e["name"]: e["value"] if "value" in e else secrets[e["secretRef"]] for e in container["env"]}


async def _local_docker(spec: StageJobSpec) -> dict[str, str]:
    seen: dict[str, str] = {}

    async def docker(
        *args: str, timeout: float = 120.0, env: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        if args[0] == "run":
            # `--env NAME=value`, or `--env NAME` with its value in docker's own environment (#439).
            named = [args[i + 1] for i, a in enumerate(args) if a == "--env"]
            seen.update(dict(n.split("=", 1) for n in named if "=" in n))
            seen.update({n: (env or {})[n] for n in named if "=" not in n})
        return (1, "", "no such container") if args[0] == "inspect" else (0, "", "")

    executor = LocalDockerExecutor(network="choregos_net")
    executor._run = docker  # type: ignore[method-assign]
    await executor.start(spec)
    return seen


async def _tekton(spec: StageJobSpec) -> dict[str, str]:
    task = yaml.safe_load(TEKTON_TASK.read_text(encoding="utf-8"))
    [step] = [s for s in task["spec"]["steps"] if s["name"] == "run"]
    env = _kubernetes_env([*task["spec"]["stepTemplate"]["env"], *step.get("env", [])], spec)
    params = {"$(params.run-id)": spec.run_id, "$(params.api-url)": spec.api_url}
    return {name: params.get(value, value) for name, value in env.items()}


EXECUTORS: dict[str, Callable[[StageJobSpec], Awaitable[dict[str, str]]]] = {
    "k8s_job": _k8s_job,
    "aca": _aca,
    "local_docker": _local_docker,
    "tekton": _tekton,
}


async def _as_the_executor_sets_them(executor: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The run's variables in the runner's process environment, as `executor` puts them there."""
    monkeypatch.delenv("CHOREGOS_EGRESS_AUTH", raising=False)
    for name, value in (await EXECUTORS[executor](_spec())).items():
        if name.startswith("CHOREGOS_RUN_"):
            monkeypatch.setenv(name, value)


@pytest.mark.parametrize("executor", sorted(EXECUTORS))
async def test_the_executor_hands_the_run_secrets_to_the_runner(executor: str) -> None:
    """The premise of every test below: the secrets really are in the runner's environment."""
    env = await EXECUTORS[executor](_spec())
    assert {name: env.get(name) for name in RUN_SECRETS} == RUN_SECRETS


# ───────────────────────── what a process the runner starts sees ─────────────────────────


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


#: The run's credentials, by the label the probe prints them under.
LABELS = {"run-token": RUN_TOKEN, "remote-token": REMOTE_TOKEN, "gateway-key": GATEWAY_KEY}

#: Run in the agent's process (or a check's, or a job's): which variables hold each secret, by
#: digest so that the probe itself carries none, and which `CHOREGOS_RUN_*` variables exist. Printed
#: as `label -> names`, a shape the journal's redaction leaves readable (`NAME=value` is masked).
PROBE = (
    "import hashlib, os, sys\n"
    "for arg in sys.argv[1:]:\n"
    "    label, digest = arg.split('=', 1)\n"
    "    held = sorted(\n"
    "        k for k, v in os.environ.items() if hashlib.sha256(v.encode()).hexdigest() == digest\n"
    "    )\n"
    "    print(label + ' -> ' + ','.join(held))\n"
    "print('run-variables -> ' + ','.join(sorted(k for k in os.environ if k.startswith('CHOREGOS_RUN_'))))\n"
)


def _probe() -> list[str]:
    return [sys.executable, "-c", PROBE, *(f"{label}={_digest(value)}" for label, value in LABELS.items())]


def _read_probe(output: str) -> dict[str, str]:
    pairs = (line.partition(" ->") for line in output.splitlines() if " ->" in line)
    return {label.strip(): names.strip() for label, _, names in pairs}


#: What the agent's process must see: its run id, its gateway key under its backend's variable, and
#: nothing of the run's credentials.
AGENT_SEES = {
    "run-token": "",
    "remote-token": "",
    "gateway-key": "OPENAI_API_KEY",
    "run-variables": "CHOREGOS_RUN_ID",
}


def _record_launches(monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """The environment `AcpClient` starts the agent with, and the one the confinement computes."""
    launched: list[dict[str, str]] = []
    confined: list[dict[str, str]] = []

    class _Recording(AcpClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            launched.append(dict(self.env))

    endpoint_urls = confine.endpoint_urls

    def _spy(env: Any) -> list[str]:
        confined.append(dict(env))
        return endpoint_urls(env)

    monkeypatch.setattr(runner_module, "AcpClient", _Recording)
    monkeypatch.setattr(confine, "endpoint_urls", _spy)
    return launched, confined


def _settings(tmp_path: Path) -> RunnerSettings:
    """Read from the environment, as `choregos-runner run` does: the run's secrets included."""
    return RunnerSettings(
        run_id="run-test-1",
        api_url="http://localhost:0/internal",
        workspace=tmp_path / "workspace",
        dry_run=False,
        tools_url=f"http://127.0.0.1:{free_port()}/mcp",
    )


async def _run_the_agent(stage_input: StageInput, settings: RunnerSettings) -> dict[str, str]:
    """A whole run whose agent runs the probe in its own process; returns what the probe saw."""
    agent_script(
        {"turns": [{"runs": [_probe()], "result": valid_result(summary="{grep}")}]},
        stage_input,
        settings.workspace,
    )
    client = FakeInternalClient(stage_input)
    outcome = await Runner(settings, client).run()  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.OK, outcome.detail
    return _read_probe(client.results[0].summary)


def _assert_holds_no_run_secret(env: dict[str, str]) -> None:
    assert not {name for name in env if name.startswith("CHOREGOS_RUN_")} - {"CHOREGOS_RUN_ID"}, sorted(env)
    for value in (RUN_TOKEN, REMOTE_TOKEN):
        assert value not in json.dumps(env)


@pytest.mark.parametrize("executor", sorted(EXECUTORS))
async def test_the_agent_is_started_without_the_run_secrets(
    executor: str, monkeypatch: pytest.MonkeyPatch, stage_input: StageInput, tmp_path: Path
) -> None:
    await _as_the_executor_sets_them(executor, monkeypatch)
    launched, confined = _record_launches(monkeypatch)
    settings = _settings(tmp_path)
    assert (settings.run_token, settings.remote_token, settings.gateway_key) == (
        RUN_TOKEN,
        REMOTE_TOKEN,
        GATEWAY_KEY,
    ), "the runner itself holds them: they are its own"

    seen = await _run_the_agent(stage_input, settings)

    [agent_env] = launched
    [confined_env] = confined
    for env in (agent_env, confined_env):
        _assert_holds_no_run_secret(env)
        assert env["CHOREGOS_RUN_ID"] == stage_input.run_id
    # The model still answers: the gateway key reaches the agent under its backend's own variable.
    assert agent_env["OPENAI_API_KEY"] == GATEWAY_KEY
    assert seen == AGENT_SEES, "what the agent's own process holds"
    assert os.environ["CHOREGOS_RUN_TOKEN"] == RUN_TOKEN, "the runner's environment is left as it is"


async def test_the_code_backend_starts_its_agent_without_the_run_secrets(
    monkeypatch: pytest.MonkeyPatch, stage_input: StageInput, tmp_path: Path
) -> None:
    """An agent written in code (ADR 0063) is an ACP agent in its own image: the same launch."""
    await _as_the_executor_sets_them("k8s_job", monkeypatch)
    launched, _ = _record_launches(monkeypatch)
    code = stage_input.model_copy(update={"agent": AgentRef(backend="code", launch=stage_input.agent.launch)})

    seen = await _run_the_agent(code, _settings(tmp_path))

    [agent_env] = launched
    _assert_holds_no_run_secret(agent_env)
    assert agent_env["CHOREGOS_MAX_TURNS"] == str(code.budget.max_turns), "the code backend's launch"
    assert seen == AGENT_SEES


async def test_the_checks_run_the_agents_code_without_the_run_secrets(
    monkeypatch: pytest.MonkeyPatch, stage_input: StageInput, tmp_path: Path
) -> None:
    """The project's test command runs the code the agent wrote, and a failing check's output is
    the next prompt: a test that prints the token would hand it to the agent."""
    await _as_the_executor_sets_them("k8s_job", monkeypatch)
    project = stage_input.project.model_copy(
        update={"test_command": shlex.join(_probe()), "lint_command": "", "typecheck_command": ""}
    )

    report = await run_checks(stage_input.model_copy(update={"project": project}), tmp_path)

    [tests] = [check for check in report.checks if check.name == "tests"]
    assert tests.ok, tests.output
    assert _read_probe(tests.output) == {
        "run-token": "",
        "remote-token": "",
        "gateway-key": "",
        "run-variables": "CHOREGOS_RUN_ID",
    }


async def test_a_job_still_gets_its_run_token_and_no_other_secret(
    monkeypatch: pytest.MonkeyPatch, stage_input: StageInput, tmp_path: Path
) -> None:
    """ADR 0056: a job is the organisation's own command, given its run token on purpose to call its
    connectors through the internal API. It gets no model, so no gateway key, and no remote token."""
    await _as_the_executor_sets_them("k8s_job", monkeypatch)
    job = stage_input.model_copy(
        update={
            "repo": None,
            "agent": AgentRef(backend="job"),
            "model": ModelRef(litellm_model="none", base_url=""),
            "gateway_key": None,
            "job": JobSpec(command=_probe()),
            "project": stage_input.project.model_copy(
                update={"test_command": "", "lint_command": "", "typecheck_command": ""}
            ),
        }
    )
    client = FakeInternalClient(job)

    outcome = await Runner(_settings(tmp_path), client).execute(job, client)  # type: ignore[arg-type]

    assert outcome.exit_code is Exit.OK, outcome.detail
    [finished] = [event for event in client.events if event["type"] == "job.finished"]
    assert _read_probe(finished["payload"]["stdout"]) == {
        "run-token": "CHOREGOS_RUN_TOKEN",
        "remote-token": "",
        "gateway-key": "",
        "run-variables": "CHOREGOS_RUN_ID,CHOREGOS_RUN_TOKEN",
    }


# ───────────────────────── the function every launch path starts from ─────────────────────────


def test_every_run_variable_but_the_run_id_is_left_out() -> None:
    environ = {
        **RUN_SECRETS,
        # A credential added tomorrow under the same prefix is left out until somebody says otherwise.
        "CHOREGOS_RUN_SOMETHING_NEW": "unknown",
        "CHOREGOS_RUN_ID": "run-1",
        "CHOREGOS_RUNNER_CONFINEMENT": "enforce",
        "CHOREGOS_API_URL": "http://api.invalid",
        # ADR 0115: the proxy variables carry the egress token, which the agent's tools need.
        "HTTPS_PROXY": "http://run-1:egress-token@proxy:3128",
        "PATH": "/usr/bin",
    }
    assert agent_base_environment(environ) == {
        "CHOREGOS_RUN_ID": "run-1",
        "CHOREGOS_RUNNER_CONFINEMENT": "enforce",
        "CHOREGOS_API_URL": "http://api.invalid",
        "HTTPS_PROXY": "http://run-1:egress-token@proxy:3128",
        "PATH": "/usr/bin",
    }


def test_the_acp_client_starts_from_it_and_its_own_variables_win(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, value in RUN_SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CHOREGOS_RUN_ID", "run-1")
    client = AcpClient(["agent"], cwd=str(tmp_path), env={"OPENAI_API_KEY": GATEWAY_KEY, "PATH": "/opt/bin"})
    _assert_holds_no_run_secret(client.env)
    assert client.env["CHOREGOS_RUN_ID"] == "run-1"
    assert client.env["OPENAI_API_KEY"] == GATEWAY_KEY and client.env["PATH"] == "/opt/bin"
    assert {name: os.environ[name] for name in RUN_SECRETS} == RUN_SECRETS, "nothing removed from the runner"

"""A hosted container writes in the volume it shares with the runner, in Docker (S41-18, S41-23).

`LocalDockerExecutor.start` is the real one: it makes the volume, mounts it at `/hosted-workspace`
in the runner and at `/workspace` in the agent's container, and gives that container `--group-add
1000`. The runner is the runner's own dependencies and its own `export_tree` (the stage of
`docker/runner.Dockerfile` that installs `choregos-runner`, with the same user and the same
`/hosted-workspace`), run on a small working tree that holds a git credential in its `.git/config`.
The agent is an image that runs as uid 12345, not 1000: only the group lets it write.

What is observed, from the volume and from `docker inspect`:

- the container overwrote a file the runner copied, created a file in a directory the runner made,
  and deleted another (the runner read the result back from its side of the volume);
- the same uid without `--group-add` cannot write there: the guard that makes it work is the group;
- no git credential, no run token, no gateway key reached the container: not in its environment,
  not in its mounts, not in the tree it saw.
"""

from __future__ import annotations

import asyncio
import io
import json
import secrets
import subprocess
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from choregos_adapters.executor.local_docker import LocalDockerExecutor, _workspace_volume
from choregos_adapters.oci import BuildRequest, LocalDockerBuilder, OciRegistryClient
from choregos_core.domain import AgentContainer, StageJobSpec

pytestmark = [pytest.mark.hosted_docker, pytest.mark.asyncio]

ROOT = Path(__file__).resolve().parents[3]
NETWORK = "s41-23-net"
RUNNER_BUILDER = "s41-23-runner-builder"
RUNNER_IMAGE = "s41-23-runner-lite"
AGENT_UID = 12345

#: The runner's side: a working tree with a credential in `.git/config`, exported as the runner does.
RUNNER = r"""
import json, os, pathlib, time
from choregos_runner.hosted_workspace import export_tree

ws = pathlib.Path("/tmp/ws")
(ws / "src" / "pkg").mkdir(parents=True)
(ws / ".git").mkdir()
(ws / "src" / "app.py").write_text("print('v1')\n")
(ws / "src" / "pkg" / "mod.py").write_text("x = 1\n")
(ws / "README.md").write_text("readme\n")
(ws / ".git" / "config").write_text(
    '[remote "origin"]\n\turl = https://x-access-token:%s@git.example/r.git\n'
    % os.environ["CHOREGOS_GIT_TOKEN"]
)
shared = pathlib.Path("/hosted-workspace")
print("EXPORTED", export_tree(ws, shared), flush=True)
(shared / "exported").write_text("")
for _ in range(250):
    if (shared / "agent.done").exists():
        break
    time.sleep(0.2)
tree = {}
for path in sorted(shared.rglob("*")):
    if path.is_file():
        info = path.stat()
        tree[str(path.relative_to(shared))] = {
            "text": path.read_text(), "uid": info.st_uid, "gid": info.st_gid, "mode": info.st_mode & 0o7777,
        }
dirs = {str(p.relative_to(shared)): p.stat().st_mode & 0o7777 for p in shared.rglob("*") if p.is_dir()}
print("TREE " + json.dumps({"files": tree, "dirs": dirs}), flush=True)
time.sleep(600)
"""

#: The agent's side: waits for the copy, changes it, and reports what it is and what it saw.
AGENT = r"""
import json, os, pathlib, time

w = pathlib.Path("/workspace")
for _ in range(600):
    if (w / "exported").exists():
        break
    time.sleep(0.1)
report = {"uid": os.getuid(), "gids": os.getgroups(), "errors": []}
for step in (
    lambda: (w / "src" / "app.py").write_text("print('v2, by the agent')\n"),
    lambda: (w / "src" / "pkg" / "new.py").write_text("y = 2\n"),
    lambda: (w / "README.md").unlink(),
):
    try:
        step()
    except OSError as error:
        report["errors"].append(str(error))
report["git"] = (w / ".git").exists()
report["env"] = dict(os.environ)
report["tree"] = sorted(str(p.relative_to(w)) for p in w.rglob("*"))
(w / "agent-report.json").write_text(json.dumps(report))
(w / "agent.done").write_text("")
time.sleep(600)
"""

AGENT_DOCKERFILE = f"""FROM python:3.12-slim-bookworm
RUN useradd --uid {AGENT_UID} --create-home agent
COPY agent.py /app/agent.py
USER {AGENT_UID}
CMD ["python", "/app/agent.py"]
"""

#: The tail of `docker/runner.Dockerfile`'s identity, on its `builder` stage (which has the runner's
#: dependencies and nothing else): uid 1000, the shared directory it creates for a hosted container.
RUNNER_DOCKERFILE = f"""FROM {RUNNER_BUILDER}
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 HOME=/workspace
RUN useradd --uid 1000 --create-home --home-dir /workspace --shell /bin/bash choregos \\
 && mkdir -p /workspace /hosted-workspace && chown -R 1000:1000 /workspace /hosted-workspace \\
 && chmod 2775 /hosted-workspace
USER 1000
WORKDIR /workspace
"""


def run_docker(*args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=timeout)


class QuietExecutor(LocalDockerExecutor):
    """The real executor, but for its housekeeping: removing old finished run containers of the
    machine is not something a test does on a developer's Docker."""

    async def _prune_finished(self) -> None:
        return None


@pytest.fixture(scope="module")
def network(docker: str) -> Iterator[str]:
    subprocess.run(["docker", "network", "create", NETWORK], capture_output=True, check=False)
    yield NETWORK
    subprocess.run(["docker", "network", "rm", NETWORK], capture_output=True, check=False)


@pytest.fixture(scope="module")
def runner_image(docker: str) -> str:
    subprocess.run(
        [
            "docker", "build", "--quiet", "--target", "builder", "--tag", RUNNER_BUILDER,
            "--file", str(ROOT / "docker" / "runner.Dockerfile"), str(ROOT),
        ],
        check=True,
        capture_output=True,
        timeout=1500,
    )  # fmt: skip
    subprocess.run(
        ["docker", "build", "--quiet", "--tag", RUNNER_IMAGE, "-"],
        input=RUNNER_DOCKERFILE,
        text=True,
        check=True,
        capture_output=True,
        timeout=300,
    )
    return RUNNER_IMAGE


@pytest.fixture(scope="module")
def agent_image(registry: str) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("agent.py", AGENT)
    request = BuildRequest(
        "sha256:" + secrets.token_hex(32), AGENT_DOCKERFILE, buffer.getvalue(), f"{registry}/s41-23-writer"
    )
    return asyncio.run(LocalDockerBuilder(OciRegistryClient()).build(request)).image


def logs(name: str) -> str:
    process = run_docker("logs", name, check=False)
    return process.stdout + process.stderr


async def wait_for(name: str, marker: str, seconds: float = 90) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        text = logs(name)
        if marker in text:
            return text
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"`{marker}` never appeared in the logs of {name}:\n{logs(name)[-1500:]}\n"
        f"the agent said:\n{logs(name + '-agent')[-1500:]}"
    )


async def test_a_container_changes_what_the_runner_copied_and_holds_no_credential(
    network: str, runner_image: str, agent_image: str
) -> None:
    git_token, run_token, gateway_key, remote_token = (
        "s41-23-" + kind + "-" + secrets.token_hex(8) for kind in ("git", "run", "gw", "remote")
    )
    run_id = "s41-23-" + secrets.token_hex(3)
    executor = QuietExecutor(network=network)
    spec = StageJobSpec(
        run_id=run_id,
        project_slug="demo",
        namespace="default",
        runner_image=runner_image,
        api_url="http://unreachable.invalid",
        run_token=run_token,
        gateway_key=gateway_key,
        remote_token=remote_token,
        env={"CHOREGOS_GIT_TOKEN": git_token},
        agent_container=AgentContainer(image=agent_image, workspace="write"),
        runner_command=["python", "-c", RUNNER],
    )
    ref = await executor.start(spec)
    agent = f"{ref.name}-agent"
    volume = _workspace_volume(ref.name)
    try:
        text = await wait_for(ref.name, "TREE ")  # the runner prints it once the agent is done
        payload = json.loads(text.split("TREE ", 1)[1].splitlines()[0])
        files, dirs = payload["files"], payload["dirs"]

        # The container changed the files the runner copied, as a uid that is not the runner's.
        assert files["src/app.py"]["text"] == "print('v2, by the agent')\n"
        assert files["src/pkg/new.py"]["text"] == "y = 2\n"
        assert "README.md" not in files, "deleted by the container"
        assert files["src/pkg/mod.py"]["text"] == "x = 1\n", "left alone"
        report = json.loads(files["agent-report.json"]["text"])
        assert report["errors"] == []
        assert report["uid"] == AGENT_UID and 1000 in report["gids"], report
        assert files["src/app.py"]["gid"] == 1000 and files["src/app.py"]["mode"] & 0o020, "group-writable"
        assert dirs["src"] & 0o2020 == 0o2020 and dirs["src/pkg"] & 0o2020 == 0o2020, dirs

        # The same user without the group cannot: that is what `--group-add 1000` is for.
        def writes(*group: str) -> subprocess.CompletedProcess[str]:
            return run_docker(
                "run", "--rm", "--user", str(AGENT_UID), *group, "--volume", f"{volume}:/workspace",
                agent_image, "python", "-c", "open('/workspace/src/pkg/mod.py', 'a').write('# more\\n')",
                check=False,
            )  # fmt: skip

        denied = writes()
        assert denied.returncode != 0 and "PermissionError" in denied.stderr, denied.stderr[-300:]
        allowed = writes("--group-add", "1000")
        assert allowed.returncode == 0, allowed.stderr[-300:]

        # No credential reached the container.
        secrets_of_the_run = (git_token, run_token, gateway_key, remote_token)
        assert report["git"] is False and not any(path.startswith(".git") for path in report["tree"])
        inspected = json.loads(run_docker("inspect", agent).stdout)[0]
        for secret in secrets_of_the_run:
            assert secret not in json.dumps(inspected), "not in the container's configuration"
            assert secret not in json.dumps(report["env"]), "not in the environment it saw"
            assert not any(
                secret in item["text"] for name, item in files.items() if name != "agent-report.json"
            ), "not in the tree it worked in"
        host = inspected["HostConfig"]
        assert host["GroupAdd"] == ["1000"] and host["CapDrop"] == ["ALL"]
        runner = json.loads(run_docker("inspect", ref.name).stdout)[0]
        assert host["NetworkMode"] == f"container:{runner['Id']}", "the runner's network, and no other"
        assert [mount["Destination"] for mount in inspected["Mounts"]] == ["/workspace"]
        runner_env = runner["Config"]["Env"]
        assert any(git_token in line for line in runner_env), "the credential is the runner's own"
    finally:
        await executor.cancel(ref)
        run_docker("volume", "rm", "--force", volume, check=False)

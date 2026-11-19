"""The agent in a container of its own, with a REAL harness and a REAL model (S65-04a, ADR 0146).

For each harness the runner image installs (`claude-agent-acp`, `opencode`, `gemini`), a pod is made
with Docker: the runner's container on a network with no way out but the pod's egress proxy (a third
container, `agent_shape/egress_proxy.py`), and the agent's container in the runner's network namespace,
as another uid (1001, group 1000), with a read-only root, no capability and no secret, running
`choregos-runner launcher`. Both mount `/workspace` and `/exchange`, and this checkout's `packages/`
over the image's, so the code under test is the checkout's. `agent_shape/runner_side.py` then runs one
step through `Runner.execute` with `agent_isolation=container`: fix a function, read a code word from a
credentialed MCP server, pass the project's checks (which run in the agent's container and probe it).

    CHOREGOS_LIVE_LITELLM_URL=https://… CHOREGOS_LIVE_LITELLM_KEY=sk-… \\
    CHOREGOS_LIVE_RUNNER_IMAGE=<registry>/tranibor-runner:<version> \\
    uv run pytest tests/live/test_agent_shape_live.py -m live -rs

Optional: `CHOREGOS_LIVE_SHAPE_MODEL` (default `choregos-claude-haiku`, the cheapest model of the dev
gateway's live-tests key), `CHOREGOS_LIVE_SHAPE_RESULTS` (a directory that keeps each harness's JSON
report), `CHOREGOS_LIVE_SHAPE_LAUNCH_ENV` (a JSON object added to the step's `agent.launch.env`, to try
a harness's setting without changing its backend), `CHOREGOS_LIVE_SHAPE_PROVIDER_MODEL` (the model behind
an alias, for a backend that checks the family), `CHOREGOS_LIVE_SHAPE_GATEWAY_NETWORK` (a Docker network the
gateway is on). Skipped without the gateway's variables, without Docker or without an image.
Every container, network and volume made here carries the label `s65a`, and is removed after.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from .conftest import require

pytestmark = pytest.mark.live

ROOT = Path(__file__).resolve().parents[2]
SHAPE = Path(__file__).resolve().parent / "agent_shape"
HARNESSES = ("claude-code", "opencode", "gemini-cli")
LABEL = "s65a"
LAUNCHER_PORT = "8766"


def docker(*args: str, env: dict[str, str] | None = None, check: bool = True, timeout: float = 120) -> str:
    done = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=timeout,
        check=False,
    )
    if check and done.returncode != 0:
        raise AssertionError(f"docker {args[0]} failed: {done.stderr[-800:]}")
    return done.stdout.strip()


def nocopy(volume: str, target: str) -> str:
    """A volume Docker does not fill with the image's directory, as an emptyDir is never filled."""
    return f"type=volume,src={volume},dst={target},volume-nocopy"


def cgroup_of(container: str) -> dict[str, Any]:
    files = "cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/cpu.stat"
    raw = docker("exec", container, "sh", "-c", files, check=False)
    lines = raw.splitlines()
    try:
        stat = dict(line.split() for line in lines[1:])
        peak, cpu = int(lines[0]) / 2**20, int(stat["usage_usec"]) / 1e6
        return {"memory_peak_mib": round(peak, 1), "cpu_seconds": round(cpu, 2)}
    except (IndexError, ValueError, KeyError):
        return {"raw": raw[:200]}


class Gateway(dict[str, str]):
    """The gateway's variables, whose `repr` holds no value: pytest prints a failing test's
    arguments, and a CI log would otherwise carry the key."""

    def __repr__(self) -> str:
        return f"Gateway(url={self.get('CHOREGOS_LIVE_LITELLM_URL', '')!r}, key=<redacted>)"


@pytest.fixture(scope="module")
def gateway() -> Gateway:
    env = require("CHOREGOS_LIVE_LITELLM_URL", "CHOREGOS_LIVE_LITELLM_KEY")
    info = None
    if shutil.which("docker"):
        info = subprocess.run(["docker", "info"], capture_output=True, check=False)
    if info is None or info.returncode:
        pytest.skip("the agent's shape needs Docker")
    image = os.environ.get("CHOREGOS_LIVE_RUNNER_IMAGE", "")
    if not image:
        pytest.skip("CHOREGOS_LIVE_RUNNER_IMAGE names no runner image")
    if docker("image", "inspect", image, check=False) == "[]":
        docker("pull", "--quiet", image, timeout=1800)
    return Gateway(
        {**env, "image": image, "model": os.environ.get("CHOREGOS_LIVE_SHAPE_MODEL", "choregos-claude-haiku")}
    )


@pytest.fixture
def results(tmp_path: Path) -> Path:
    kept = os.environ.get("CHOREGOS_LIVE_SHAPE_RESULTS")
    path = Path(kept) if kept else tmp_path / "results"
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o777)  # written by the containers' uids
    return path


@pytest.fixture
def pod(gateway: Gateway, results: Path) -> Iterator[dict[str, str]]:
    """The egress proxy, the runner's container and the agent's, with their network and volumes."""
    tag = uuid.uuid4().hex[:8]
    names = {
        "net": f"s65a-pod-{tag}",
        "egress": f"s65a-egress-{tag}",
        "runner": f"s65a-runner-{tag}",
        "agent": f"s65a-agent-{tag}",
        "workspace": f"s65a-workspace-{tag}",
        "exchange": f"s65a-exchange-{tag}",
    }
    image, label = gateway["image"], ["--label", LABEL]
    packages = f"{ROOT / 'packages'}:/app/packages:ro"
    shared = ["--mount", nocopy(names["workspace"], "/workspace")]
    shared += ["--mount", nocopy(names["exchange"], "/exchange")]
    try:
        docker("network", "create", "--internal", *label, names["net"])
        for volume in ("workspace", "exchange"):
            docker("volume", "create", *label, names[volume])
        # What an emptyDir and the pod's fsGroup give on a cluster: empty volumes of group 1000, setgid.
        # (Docker fills a new volume with the image's `/workspace`, which the build leaves non-empty.)
        docker(
            "run", "--rm", *label, "--user", "0", "--entrypoint", "sh",
            *shared, image, "-c", "find /workspace /exchange -mindepth 1 -delete"
            " && chown 1000:1000 /workspace /exchange && chmod 2775 /workspace /exchange",
        )  # fmt: skip
        docker(
            "run", "-d", *label, "--name", names["egress"], "--entrypoint", "python3",
            "-v", f"{SHAPE}:/shape:ro", "-v", f"{results}:/results", image,
            "/shape/egress_proxy.py", "--log", f"/results/egress-{tag}.jsonl",
        )  # fmt: skip
        docker("network", "connect", names["net"], names["egress"])
        if gateway_network := os.environ.get("CHOREGOS_LIVE_SHAPE_GATEWAY_NETWORK"):
            # A gateway on a network of its own (the nightly's compose stack): the pod reaches it
            # through its egress proxy, as everything else.
            docker("network", "connect", gateway_network, names["egress"])
        egress_token, run_token = secrets.token_hex(16), secrets.token_hex(16)
        proxy = f"http://s65a-run:{egress_token}@{names['egress']}:3128"
        # The values travel in docker's environment, never on its command line.
        secret_env = {
            "CHOREGOS_RUN_GATEWAY_KEY": gateway["CHOREGOS_LIVE_LITELLM_KEY"],
            "CHOREGOS_RUN_TOKEN": run_token,
            "CHOREGOS_RUN_S65A_EGRESS": egress_token,
            "HTTPS_PROXY": proxy,
            "HTTP_PROXY": proxy,
        }
        docker(
            "run", "-d", "--init", *label, "--name", names["runner"], "--network", names["net"],
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            *(arg for name in secret_env for arg in ("-e", name)),
            "-e", "NO_PROXY=127.0.0.1,localhost", "-e", "TMPDIR=/exchange",
            "-e", f"S65A_LAUNCH_ENV={os.environ.get('CHOREGOS_LIVE_SHAPE_LAUNCH_ENV', '{}')}",
            "-e", f"S65A_GATEWAY_URL={gateway['CHOREGOS_LIVE_LITELLM_URL']}",
            "-e", f"S65A_MODEL={gateway['model']}",
            "-e", f"S65A_PROVIDER_MODEL={os.environ.get('CHOREGOS_LIVE_SHAPE_PROVIDER_MODEL', '')}",
            "-e", "CHOREGOS_RUNNER_AGENT_ISOLATION=container",
            "-e", f"CHOREGOS_RUNNER_LAUNCHER_PORT={LAUNCHER_PORT}",
            *shared,
            "-v", packages, "-v", f"{SHAPE}:/shape:ro", "-v", f"{results}:/results",
            "--entrypoint", "sleep", image, "infinity",
            env=secret_env,
        )  # fmt: skip
        # The agent's container as the SandboxTemplate makes it (S65-04i): uid 1001, group 1000,
        # read-only root, its own /tmp, no secret, the launcher as its command.
        docker(
            "run", "-d", *label, "--name", names["agent"], "--network", f"container:{names['runner']}",
            "--user", "1001:1000", "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "-e", "TMPDIR=/exchange", "-e", "HOME=/exchange",
            *shared, "-v", packages,
            image, "launcher", "--port", LAUNCHER_PORT,
        )  # fmt: skip
        yield {**names, "tag": tag}
    finally:
        for container in ("agent", "runner", "egress"):
            docker("rm", "-f", names[container], check=False)
        docker("network", "rm", names["net"], check=False)
        for volume in ("workspace", "exchange"):
            docker("volume", "rm", "-f", names[volume], check=False)


def harnesses() -> list[Any]:
    """The three harnesses; `gemini-cli` is expected to fail on a model that is not Gemini (#1990)."""
    model = os.environ.get("CHOREGOS_LIVE_SHAPE_MODEL", "choregos-claude-haiku")
    gemini = pytest.mark.xfail(
        "gemini" not in model.lower(),
        reason="#1990: LiteLLM's Gemini adapter gives parallel calls of one function one id (Claude)",
        strict=False,
    )
    return [pytest.param(name, marks=gemini) if name == "gemini-cli" else name for name in HARNESSES]


@pytest.mark.parametrize("backend", harnesses())
def test_a_real_harness_works_from_its_own_container(
    backend: str, pod: dict[str, str], results: Path, gateway: Gateway
) -> None:
    out = f"/results/{backend}.json"
    driver = ["docker", "exec", pod["runner"], "/app/.venv/bin/python", "/shape/runner_side.py"]
    done = subprocess.run(
        [*driver, "--backend", backend, "--out", out],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    report_path = results / f"{backend}.json"
    assert report_path.is_file(), f"no report: {done.stderr[-2000:]}"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["agent_container"] = cgroup_of(pod["agent"])
    egress_log = results / f"egress-{pod['tag']}.jsonl"
    logged = egress_log.read_text().splitlines() if egress_log.exists() else []
    report["pod_egress"] = [json.loads(line) for line in logged]
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")

    # The harness was started by the launcher and finished the step.
    isolation = [e for e in report["events"] if e["type"] == "agent.isolation"]
    assert isolation and isolation[0]["payload"]["mode"] == "container", isolation
    assert report["exit_code"].startswith("OK"), (report["exit_code"], report["detail"])
    assert report["result"] and report["result"]["status"] == "done", report["result"]
    # Its model: on the loopback, with the loopback key; the gateway booked the run's key.
    assert any(r["status"] == 200 for r in report["model_requests"]), report["model_requests"]
    assert report["spend_usd"] and report["spend_usd"] > 0, report["spend_usd"]
    # Its MCP server: through 4002, which added the header the agent never had.
    assert "HELIOTROPE" in (report["files"]["answer.txt"] or ""), report["files"]
    assert report["mcp"]["calls"] and all(r["authorized"] for r in report["mcp"]["requests"]), report["mcp"]
    # What the probe saw from inside the agent's container.
    probe = report["probe"]
    assert probe, "the probe did not run in the agent's container"
    assert probe["uid"] == 1001
    assert probe["secrets_in_processes"] == [] and probe["secrets_in_files"] == [], probe
    assert not [p for p in probe["processes"] if p.get("uid") == 1000], "a process of the runner is visible"
    assert probe["egress"]["direct"].startswith("refused"), probe["egress"]
    assert probe["egress"]["via_proxy"] == 200, probe["egress"]

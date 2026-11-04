"""Does the runner image carry headroom pinned, hash-checked, offline, and runnable? (ADR 0055, S37-06)

headroom sits between an agent and the gateway and rewrites what the model reads. A wheel that
is not the one locked would see every request of every run, so the image installs from a lock that
carries a hash for each wheel. Its model is fetched at build time, and the image forbids headroom
any egress at run time.

Building the image is the CI's job. These tests keep a later edit from removing a check without
anyone noticing.
"""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker/runner.Dockerfile"
LOCK_IN = ROOT / "docker/headroom/requirements.in"
LOCK = ROOT / "docker/headroom/requirements.txt"
STAGE = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?\S+(?:\s+AS\s+(\S+))?\s*$", re.M | re.I)
PIN = re.compile(r"^headroom-ai\[proxy,code\]==(\d+\.\d+\.\d+)$", re.M)


def _stages() -> dict[str, str]:
    text = DOCKERFILE.read_text(encoding="utf-8")
    starts = list(STAGE.finditer(text))
    ends = [start.start() for start in starts[1:]] + [len(text)]
    return {
        start.group(1) or f"#{i}": text[start.end() : end]
        for i, (start, end) in enumerate(zip(starts, ends, strict=True))
    }


def _requirements() -> list[str]:
    """Each requirement of the lock, with its continuation lines joined."""
    text = LOCK.read_text(encoding="utf-8").replace("\\\n", " ")
    return [line for line in text.splitlines() if line and not line.lstrip().startswith("#")]


def test_the_lock_pins_headroom_and_a_hash_for_every_wheel() -> None:
    pinned = PIN.search(LOCK_IN.read_text(encoding="utf-8"))
    assert pinned is not None
    requirements = _requirements()
    assert any(line.startswith(f"headroom-ai=={pinned.group(1)} ") for line in requirements)
    for line in requirements:
        assert re.match(r"^[A-Za-z0-9._-]+==\S+ ", line), line
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", line), line


def test_the_lock_is_compiled_by_the_command_renovate_reruns() -> None:
    """Renovate's `pip-compile` manager reruns the command in the lock's header, and
    `make headroom-lock` must be that same command: hashes, and nothing younger than 14 days."""
    header = LOCK.read_text(encoding="utf-8").splitlines()[1].removeprefix("#").strip()
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8").replace("\\\n", " ")
    target = re.search(r"^headroom-lock:.*\n\t\$\(UV\) (.+)$", makefile, re.M)
    assert target is not None
    assert "uv " + " ".join(target.group(1).split()) == header
    assert "--generate-hashes" in header.split()
    assert "--exclude-newer P14D" in header

    renovate = json.loads((ROOT / "renovate.json").read_text(encoding="utf-8"))
    assert renovate["pip-compile"]["managerFilePatterns"] == ["/^docker/headroom/requirements\\.txt$/"]
    rules = [
        rule for rule in renovate["packageRules"] if "docker/headroom/**" in rule.get("matchFileNames", [])
    ]
    automerge = {rule["matchManagers"][0]: rule.get("automerge") for rule in rules}
    assert automerge == {"pip-compile": False, "pip_requirements": None}
    assert next(rule for rule in rules if rule["matchManagers"] == ["pip_requirements"])["enabled"] is False


def test_the_lock_carries_a_ca_bundle_no_older_than_the_workspace_one() -> None:
    """The 14-day wait kept a revoked root CA in the runner image after uv.lock had dropped it
    (GHSA-6q48-6c7w-9gw2): certifi is exempt from the wait, and never lags the workspace lock."""
    header = LOCK.read_text(encoding="utf-8").splitlines()[1]
    assert "--exclude-newer-package certifi=false" in header
    workspace = re.search(r'^name = "certifi"\nversion = "([\d.]+)"', (ROOT / "uv.lock").read_text(), re.M)
    pinned = next(line for line in _requirements() if line.startswith("certifi=="))
    assert workspace is not None
    headroom_version = pinned.split("==")[1].split()[0]

    def as_tuple(version: str) -> tuple[int, ...]:
        return tuple(int(part) for part in version.split("."))

    assert as_tuple(headroom_version) >= as_tuple(workspace.group(1)), (headroom_version, workspace.group(1))


def test_the_stage_installs_by_hash_and_loads_its_model_offline() -> None:
    stage = _stages()["headroom"]
    install = re.search(r"uv pip install (.+?)\n(?!\s+-)", stage.replace("\\\n", " "), re.S)
    assert install is not None
    assert "--require-hashes" in install.group(1)
    assert "-r /tmp/headroom-requirements.txt" in install.group(1)
    assert "COPY docker/headroom/requirements.txt /tmp/headroom-requirements.txt" in stage
    # Fetched online once, then loaded again with every Hub access forbidden.
    assert re.search(r'^\s+/opt/headroom/bin/python -c "\$warm" online;', stage, re.M)
    assert re.search(
        r"^\s+HEADROOM_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 "
        r'/opt/headroom/bin/python -c "\$warm" offline$',
        stage,
        re.M,
    )
    assert "HF_HOME=/opt/headroom/models" in stage


def test_the_runtime_image_runs_headroom_offline_and_off_the_path() -> None:
    runtime = _stages()["runtime"]
    assert "COPY --from=headroom /opt/headroom /opt/headroom" in runtime
    env = re.search(r"^ENV\s+((?:.*\\\n)*.*)$", runtime, re.M)
    assert env is not None
    assert re.search(r"\bHEADROOM_OFFLINE=1\b", env.group(1))
    # litellm, imported by headroom, fetches its price list at start unless told not to.
    assert re.search(r"\bLITELLM_LOCAL_MODEL_COST_MAP=True\b", env.group(1))
    assert re.search(r"\bHF_HOME=/opt/headroom/models\b", env.group(1))
    assert "/opt/headroom/bin" not in env.group(1), "its venv would shadow the runner's packages"
    assert re.search(r"^\s*timeout 60 headroom --version\b", runtime, re.M)


def test_the_build_proves_headroom_relays_the_gateway_budget_refusal() -> None:
    """A proxy that wrapped LiteLLM's `budget_exceeded` would make a spent key look like a silent
    agent, retried with a fresh key (S37-12). The image's own headroom is checked at build, after
    the runner is installed, with the runner's own detector."""
    runtime = _stages()["runtime"]
    copy = "COPY docker/headroom/relays_budget_refusal.py /tmp/relays_budget_refusal.py"
    run = re.search(
        r"^RUN timeout \d+ /app/\.venv/bin/python /tmp/relays_budget_refusal\.py\b", runtime, re.M
    )
    assert copy in runtime
    assert run is not None
    assert (
        runtime.index("COPY --from=builder --chown=1000:1000 /app /app") < runtime.index(copy) < run.start()
    )

    probe = (ROOT / "docker/headroom/relays_budget_refusal.py").read_text(encoding="utf-8")
    assert "from choregos_runner.budget import gateway_budget_refusal" in probe
    assert '"type": "budget_exceeded"' in probe
    # Both wires, each streamed and not.
    assert "/v1/messages" in probe and "/v1/chat/completions" in probe
    assert probe.count('"stream": True') == 2
    assert "return 1 if failed else 0" in probe

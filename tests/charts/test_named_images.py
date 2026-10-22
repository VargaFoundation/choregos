"""The images an operator names reach the API and every orchestrator worker (ADR 0056, ADR 0063)."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
DIGEST = "sha256:" + "a" * 64

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _envs(*overrides: str) -> dict[str, dict[str, str]]:
    """The plain env of the API and of each orchestrator worker, by deployment name."""
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rendered.returncode == 0, rendered.stderr
    documents: list[dict[str, Any]] = [d for d in yaml.safe_load_all(rendered.stdout) if d]
    out: dict[str, dict[str, str]] = {}
    for deployment in (d for d in documents if d.get("kind") == "Deployment"):
        name = deployment["metadata"]["name"]
        if name != "choregos-api" and not name.startswith("choregos-orchestrator"):
            continue
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        out[name] = {e["name"]: e["value"] for e in container.get("env", []) if "value" in e}
    return out


def test_nothing_is_named_by_default_and_digests_are_required() -> None:
    envs = _envs()
    assert "choregos-api" in envs and len(envs) > 1
    for name, env in envs.items():
        assert "CHOREGOS_JOB_IMAGES" not in env and "CHOREGOS_AGENT_IMAGES" not in env, name
        assert env["CHOREGOS_REQUIRE_IMAGE_DIGEST"] == "true", name


def test_the_named_images_reach_the_api_and_every_worker() -> None:
    envs = _envs(
        f"global.images.agents.triager=registry.example/agents/triager@{DIGEST}",
        f"global.images.jobs.quant=registry.example/quant@{DIGEST}",
    )
    for name, env in envs.items():
        assert json.loads(env["CHOREGOS_AGENT_IMAGES"]) == {
            "triager": f"registry.example/agents/triager@{DIGEST}"
        }, name
        assert json.loads(env["CHOREGOS_JOB_IMAGES"]) == {"quant": f"registry.example/quant@{DIGEST}"}, name


def test_a_bench_can_turn_the_digest_rule_off() -> None:
    envs = _envs("global.images.requireDigest=false")
    assert all(env["CHOREGOS_REQUIRE_IMAGE_DIGEST"] == "false" for env in envs.values())


def test_hosted_registries_and_the_signature_mode_reach_the_api_only() -> None:
    default = _envs()["choregos-api"]
    assert "CHOREGOS_HOSTED_REGISTRIES" not in default
    assert default["CHOREGOS_HOSTED_COSIGN"] == "off"
    envs = _envs(
        "global.images.hostedRegistries={ghcr.io/acme,registry.internal}",
        "global.images.hostedCosign=enforce",
    )
    assert json.loads(envs["choregos-api"]["CHOREGOS_HOSTED_REGISTRIES"]) == [
        "ghcr.io/acme",
        "registry.internal",
    ]
    assert envs["choregos-api"]["CHOREGOS_HOSTED_COSIGN"] == "enforce"
    assert all("CHOREGOS_HOSTED_COSIGN" not in env for name, env in envs.items() if name != "choregos-api")

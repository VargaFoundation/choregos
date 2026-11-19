# SPDX-License-Identifier: Apache-2.0
"""`choregos-quant` runs in the plugin's own job image, built by the core's release FROM its runner (S83-07,
ADR 0181 O4-3, ADR 0056).

The runner image no longer carries the engine; the job image is the same release's runner plus this
distribution, installed without its dependencies, which holds only because the engine imports nothing
outside the standard library. Remove the release job, let the engine import a third-party module, or put
the engine back in the runner, and a test here turns red. CI's `images` job builds the image FROM the
runner it just built and starts the command (`.github/workflows/ci.yml`).
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import yaml
from tranibor_core.publishing import PUBLISHING

PLUGIN = Path(__file__).resolve().parents[1]
ROOT = PLUGIN.parents[1]
QUANT = PLUGIN / "src" / "tranibor_investment_research" / "quant"
IMAGE = PUBLISHING.image(f"{PUBLISHING.image_prefix}-investment-research")


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def test_the_engine_needs_nothing_but_the_standard_library() -> None:
    """The job image installs the distribution `--no-deps`: a third-party import would fail in a job."""
    modules = [*QUANT.rglob("*.py"), PLUGIN / "src" / "tranibor_investment_research" / "__init__.py"]
    outside = {
        f"{path.name}: {name}"
        for path in modules
        for name in _imports(path)
        if name.partition(".")[0] not in sys.stdlib_module_names
        and not name.startswith("tranibor_investment_research.quant")
    }
    assert not outside, sorted(outside)


def test_the_distribution_declares_the_command() -> None:
    data = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["choregos-quant"] == "tranibor_investment_research.quant.cli:main"
    assert (QUANT / "schemas" / "strategies.schema.json").is_file(), (
        "the strategies' schema ships in the package"
    )


def test_the_job_image_is_the_runner_plus_the_command() -> None:
    text = (PLUGIN / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG RUNNER_IMAGE\n" in text, "no default runner: the release passes its own, by digest"
    assert "FROM ${RUNNER_IMAGE}\n" in text
    assert "--no-deps" in text and "/app/.venv/bin/python" in text
    assert "choregos-quant --help" in text and "/usr/local/bin/choregos-quant" in text
    assert text.rstrip().endswith("USER 1000"), "a job runs as the runner does, uid 1000"


def test_the_runner_no_longer_carries_the_engine() -> None:
    runner = tomllib.loads((ROOT / "packages/runner/pyproject.toml").read_text(encoding="utf-8"))
    assert not [d for d in runner["project"]["dependencies"] if "quant" in d or "investment" in d]
    dockerfile = (ROOT / "docker/runner.Dockerfile").read_text(encoding="utf-8")
    instructions = [line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in instructions if "choregos-quant" in line], "neither installed nor linked"
    assert not (ROOT / "packages" / "quant").exists() and not (ROOT / "compat" / "choregos-quant").exists()


def test_the_release_builds_it_from_its_own_runner_and_signs_it() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    build, manifest = jobs["job-image"], jobs["job-image-manifest"]
    assert build["needs"] == "manifest", "after the runner's manifest is published and signed"
    assert build["env"]["IMAGE"] == IMAGE == manifest["env"]["IMAGE"]
    steps = {s.get("uses", "").partition("@")[0]: s for s in build["steps"]}
    pushed = steps["docker/build-push-action"]["with"]
    assert pushed["file"] == "plugins/investment-research/Dockerfile"
    assert pushed["build-args"] == "RUNNER_IMAGE=${{ steps.runner.outputs.image }}"
    resolved = next(s for s in build["steps"] if s.get("id") == "runner")["run"]
    assert '"${RUNNER}:${version}"' in resolved and "@${digest}" in resolved, (
        "the runner of this tag, by digest"
    )
    assert manifest["needs"] == "job-image"
    signed = next(s for s in manifest["steps"] if s.get("name") == "sign the manifest")["run"]
    assert 'cosign sign --yes "${IMAGE}@${digest}"' in signed
    assert "job-image-manifest" in jobs["changelog"]["needs"], "the release page waits for it"


def test_ci_builds_it_from_the_runner_it_just_built() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    (step,) = [
        s for s in workflow["jobs"]["images"]["steps"] if "investment-research job image" in s.get("name", "")
    ]
    assert step["if"] == "matrix.image == 'runner'"
    assert "plugins/investment-research/Dockerfile" in step["run"] and "choregos-quant" in step["run"]

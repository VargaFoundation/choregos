# SPDX-License-Identifier: Apache-2.0
"""`tranibor-procurement` runs in the plugin's own job image, built by the core's release FROM its runner
(S85-09, ADR 0202, ADR 0056).

The image is the same release's runner plus this distribution in an environment of its own,
`/opt/procurement`, with the dependencies the lock gives, by hash. Remove the release job, give the
Dockerfile a default runner, or drop the command's check, and a test here turns red. CI's `images` job
builds the image FROM the runner it just built and runs the command on a real PDF
(`.github/workflows/ci.yml`); `tests/packaging/test_procurement_requirements_match_the_lock.py` keeps the
requirements file equal to the lock.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml
from tranibor_core.publishing import PUBLISHING

PLUGIN = Path(__file__).resolve().parents[1]
ROOT = PLUGIN.parents[1]
IMAGE = PUBLISHING.image(f"{PUBLISHING.image_prefix}-procurement")


def test_the_distribution_declares_the_command() -> None:
    data = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["tranibor-procurement"] == "tranibor_procurement.cli:main"
    assert "entry-points" not in data["project"], "no extension: nothing registers itself (ADR 0202)"


def test_the_job_image_is_the_runner_plus_the_command() -> None:
    text = (PLUGIN / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG RUNNER_IMAGE\n" in text, "no default runner: the release passes its own, by digest"
    assert "FROM ${RUNNER_IMAGE}\n" in text
    assert "ARG RUNNER_IMAGE=" not in text
    assert "ln -sf /opt/procurement/bin/tranibor-procurement /usr/local/bin/tranibor-procurement" in text
    assert "tranibor-procurement --help" in text and "choregos-runner --help" in text
    assert "cmp /tmp/runner-before.txt /tmp/runner-after.txt" in text, (
        "the runner's environment is the release's"
    )
    assert text.rstrip().endswith("USER 1000"), "a job runs as the runner does, uid 1000"


def test_the_release_builds_it_from_its_own_runner_and_signs_it() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    build, manifest = jobs["procurement-job-image"], jobs["procurement-job-image-manifest"]
    assert build["needs"] == "manifest", "after the runner's manifest is published and signed"
    assert build["env"]["IMAGE"] == IMAGE == manifest["env"]["IMAGE"]
    assert build["strategy"]["matrix"]["platform"] == ["amd64", "arm64"]
    steps = {s.get("uses", "").partition("@")[0]: s for s in build["steps"]}
    pushed = steps["docker/build-push-action"]["with"]
    assert pushed["file"] == "plugins/procurement/Dockerfile"
    assert pushed["build-args"] == "RUNNER_IMAGE=${{ steps.runner.outputs.image }}"
    resolved = next(s for s in build["steps"] if s.get("id") == "runner")["run"]
    assert '"${RUNNER}:${version}"' in resolved and "@${digest}" in resolved, (
        "the runner of this tag, by digest"
    )
    assert manifest["needs"] == "procurement-job-image"
    signed = next(s for s in manifest["steps"] if s.get("name") == "sign the manifest")["run"]
    assert 'cosign sign --yes "${IMAGE}@${digest}"' in signed
    assert "procurement-job-image-manifest" in jobs["changelog"]["needs"], "the release page waits for it"


def test_ci_builds_it_from_the_runner_it_just_built_and_reads_a_pdf() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    (step,) = [s for s in workflow["jobs"]["images"]["steps"] if "procurement job image" in s.get("name", "")]
    assert step["if"] == "matrix.image == 'runner'"
    run = step["run"]
    assert "plugins/procurement/Dockerfile" in run and "RUNNER_IMAGE" in run
    assert "tranibor-procurement" in run and " extract" in run
    assert "alderbrook-offer.pdf" in run and ".choregos/inputs" in run and "job.json" in run
    assert (PLUGIN / "tests" / "fixtures" / "alderbrook-offer.pdf").is_file()

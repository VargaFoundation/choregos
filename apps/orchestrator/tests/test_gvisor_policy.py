"""A project whose policy says gVisor gets a Kyverno policy that refuses any other pod (S41-17).

Two things had to hold for the policy to refuse anything, and neither was tested: Argo CD applies
the project's directory through its `kustomization.yaml`, so a manifest it does not list is
written to the GitOps repository and never reaches the cluster, which was the case of
`runtimeclass.yaml`; and the rule matches pods while the executor creates Jobs, which Kyverno's
autogen covers. The second is judged by the `kyverno` CLI on the Job the real executor builds for a
hosted run, its agent in a native sidecar.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_adapters.executor.k8s_job import KubernetesJobExecutor
from choregos_adapters.executor.tekton import KubernetesClient
from choregos_contracts import ProjectConfig, RepoConfig
from choregos_core import load_preset
from choregos_core.domain import AgentContainer, StageJobSpec
from choregos_orchestrator.gitops import render_project_manifests

SLUG = "billing"


def _files() -> dict[str, str]:
    config = ProjectConfig(
        slug=SLUG, org="varga", repo=RepoConfig(url="https://github.com/varga/billing.git")
    )
    policy = load_preset("team")
    assert policy.sandbox.runtime == "gvisor", "the `team` preset is the one that asks for gVisor"
    return render_project_manifests(SLUG, config, policy)


def test_argo_cd_applies_every_manifest_the_project_renders() -> None:
    files = _files()
    (kustomization,) = yaml.safe_load_all(files["kustomization.yaml"])
    assert set(kustomization["resources"]) == set(files) - {"kustomization.yaml"}
    assert "runtimeclass.yaml" in kustomization["resources"]


def _hosted_job(runtime_class: str | None) -> dict[str, Any]:
    spec = StageJobSpec(
        run_id="hosted-1",
        project_slug=SLUG,
        namespace=f"proj-{SLUG}-runners",
        runner_image="registry.internal/choregos/runner@sha256:" + "1" * 64,
        api_url="http://choregos-api",
        run_token="t",
        timeout_minutes=5,
        cpu="100m",
        memory="128Mi",
        runtime_class=runtime_class,
        agent_container=AgentContainer(
            image="registry.internal/acme/triager@sha256:" + "2" * 64,
            env={"PORT": "8088"},
            termination_grace_s=30,
            cpu="250m",
            memory="256Mi",
        ),
    )
    client = KubernetesClient("https://k8s.test", token="t", verify=False)
    job = KubernetesJobExecutor(client=client)._job(spec, "choregos-hosted-1")
    assert job["spec"]["template"]["spec"]["initContainers"], "a hosted run: the agent is a sidecar"
    return job


@pytest.mark.skipif(shutil.which("kyverno") is None, reason="no kyverno CLI on the path")
@pytest.mark.parametrize(("runtime_class", "admitted"), [(None, False), ("runc", False), ("gvisor", True)])
def test_the_policy_refuses_a_hosted_job_without_gvisor(
    tmp_path: Path, runtime_class: str | None, admitted: bool
) -> None:
    (tmp_path / "policy.yaml").write_text(_files()["runtimeclass.yaml"])
    (tmp_path / "job.yaml").write_text(yaml.safe_dump(_hosted_job(runtime_class)))
    done = subprocess.run(
        ["kyverno", "apply", str(tmp_path / "policy.yaml"), "--resource", str(tmp_path / "job.yaml")],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    found = re.search(r"pass: (\d+), fail: (\d+), warn: \d+, error: (\d+)", done.stdout)
    assert found is not None, done.stdout + done.stderr
    assert found.group(3) == "0", done.stdout
    assert int(found.group(1)) + int(found.group(2)) == 1, "the rule judged the Job, through autogen"
    assert (found.group(2) == "0") is admitted, done.stdout

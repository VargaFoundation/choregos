"""Agent pods pass the strict Kyverno rule, for both Kubernetes executors.

`infra/policies/pod-security.yaml` requires the seccomp profile at the POD level, not only
the container's; a Job or a PipelineRun that does not carry it is not admitted in a
namespace `proj-*`.
"""

from __future__ import annotations

from choregos_adapters.executor.tekton import KubernetesClient, TektonExecutor
from choregos_core.domain import StageJobSpec

from .test_k8s_job import KubernetesJobExecutor, _RecordingClient, _spec


async def test_the_kubernetes_job_carries_the_pod_context() -> None:
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    pod = job["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    conteneur = pod["containers"][0]["securityContext"]
    assert conteneur["allowPrivilegeEscalation"] is False and conteneur["capabilities"] == {"drop": ["ALL"]}


def test_the_tekton_pipelinerun_carries_the_pod_context() -> None:
    executor = TektonExecutor(KubernetesClient("https://k8s.test", token="t", verify=False))
    spec = StageJobSpec(
        run_id="r-1",
        project_slug="p",
        namespace="proj-p-runners",
        runner_image="img",
        api_url="u",
        run_token="t",
        runtime_class="gvisor",
    )
    template = executor._pipeline_run(spec, "run-r-1")["spec"]["taskRunTemplate"]["podTemplate"]
    assert template["securityContext"]["runAsNonRoot"] is True
    assert template["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert template["runtimeClassName"] == "gvisor"

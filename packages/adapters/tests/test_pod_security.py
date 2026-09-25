"""Les pods d'agent passent la règle Kyverno stricte, pour les deux exécuteurs Kubernetes.

`infra/policies/pod-security.yaml` exige le profil seccomp au niveau du POD, pas seulement
du conteneur ; un Job ou un PipelineRun qui ne le porte pas n'est pas admis dans un
namespace `proj-*`.
"""

from __future__ import annotations

from choregos_adapters.executor.tekton import KubernetesClient, TektonExecutor
from choregos_core.domain import StageJobSpec

from .test_k8s_job import KubernetesJobExecutor, _RecordingClient, _spec


async def test_le_job_kubernetes_porte_le_contexte_du_pod() -> None:
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    pod = job["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    conteneur = pod["containers"][0]["securityContext"]
    assert conteneur["allowPrivilegeEscalation"] is False and conteneur["capabilities"] == {"drop": ["ALL"]}


def test_le_pipelinerun_tekton_porte_le_contexte_du_pod() -> None:
    executeur = TektonExecutor(KubernetesClient("https://k8s.test", token="t", verify=False))
    spec = StageJobSpec(
        run_id="r-1",
        project_slug="p",
        namespace="proj-p-runners",
        runner_image="img",
        api_url="u",
        run_token="t",
        runtime_class="gvisor",
    )
    modele = executeur._pipeline_run(spec, "run-r-1")["spec"]["taskRunTemplate"]["podTemplate"]
    assert modele["securityContext"]["runAsNonRoot"] is True
    assert modele["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert modele["runtimeClassName"] == "gvisor"

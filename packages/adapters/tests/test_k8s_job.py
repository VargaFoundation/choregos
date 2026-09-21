"""Exécuteur `k8s_job` : ce que le Job porte, vérifié sans cluster."""

from __future__ import annotations

from typing import Any

from choregos_adapters.executor.k8s_job import RUNNER_POD_LABELS, KubernetesJobExecutor
from choregos_core.domain import StageJobSpec


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs.get("json")))
        return None  # rien n'existe encore : le Job et son secret sont créés


def _spec() -> StageJobSpec:
    return StageJobSpec(
        run_id="r-1",
        project_slug="demo",
        namespace="choregos",
        runner_image="harbor.example/ghcr-proxy/vargafoundation/choregos-runner:0.1.0",
        api_url="http://choregos-api:8000",
        run_token="jeton",
    )


async def test_le_pod_runner_porte_un_label_stable_pour_les_politiques_reseau() -> None:
    """Les politiques réseau sélectionnent les runners par famille : sans label stable,
    un pod runner ne sort vers rien — ou il faut ouvrir tout le namespace."""
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    labels = job["spec"]["template"]["metadata"]["labels"]
    assert labels["choregos/run-id"] == "r-1"
    assert RUNNER_POD_LABELS.items() <= labels.items()


async def test_le_pod_runner_ne_monte_aucun_jeton_kubernetes() -> None:
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["serviceAccountName"] == "choregos-runner"

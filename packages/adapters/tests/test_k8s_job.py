"""Exécuteur `k8s_job` : ce que le Job porte, vérifié sans cluster."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.executor.k8s_job import RUNNER_POD_LABELS, KubernetesJobExecutor
from choregos_adapters.registry import default_executor_kind
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


async def test_les_limites_du_pod_runner_suivent_la_configuration() -> None:
    """Sous un LimitRange à 4 Gi par conteneur, un plafond de 6 Gi fait refuser le pod."""
    client = _RecordingClient()
    executor = KubernetesJobExecutor(client=client, cpu_limit="1500m", memory_limit="4Gi")  # type: ignore[arg-type]
    await executor.start(_spec())
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    limits = job["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]
    assert limits == {"cpu": "1500m", "memory": "4Gi"}


def test_un_projet_sans_runtime_prend_l_executeur_du_deploiement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    assert default_executor_kind() == "k8s_job"
    monkeypatch.delenv("CHOREGOS_EXECUTOR_KIND")
    assert default_executor_kind() == "tekton"


def test_la_memoire_prend_l_url_et_le_jeton_du_deploiement(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CHOREGOS_MEMORY_URL` était posé par le chart et ignoré : chaque projet visait
    `ecphoria.choregos-memory`, un service qui n'existe que dans une installation."""
    from choregos_adapters import build

    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)  # la CI tourne en fakes
    monkeypatch.setenv("CHOREGOS_MEMORY_URL", "http://ecphoria:8432")
    monkeypatch.setenv("CHOREGOS_MEMORY_TOKEN", "cle")
    memory = build("memory", "ecphoria", {})
    assert memory.base_url == "http://ecphoria:8432"
    assert memory.token == "cle"

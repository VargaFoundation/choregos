"""Executor Job Kubernetes : le repli quand Tekton n'est pas disponible (§2.3).

Même contrat que Tekton : un Job par run, un Secret pour le jeton, un nettoyage automatique
par `ttlSecondsAfterFinished`.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec

from .tekton import CORE_API, KubernetesClient, _parse_time

BATCH_API = "/apis/batch/v1"
RUNNER_POD_LABELS = {"app.kubernetes.io/name": "choregos-runner", "app.kubernetes.io/component": "runner"}


class KubernetesJobExecutor:
    kind = ExecutorKind.K8S_JOB

    def __init__(
        self,
        client: KubernetesClient | None = None,
        *,
        service_account: str = "choregos-runner",
        ttl_seconds: int = 3600,
    ) -> None:
        self.client = client or KubernetesClient()
        self.service_account = service_account
        self.ttl_seconds = ttl_seconds

    def _name(self, run_id: str) -> str:
        return f"run-{run_id}"[:63].lower()

    async def start(self, spec: StageJobSpec) -> ExecRef:
        name = self._name(spec.run_id)
        existing = await self.client.request("GET", f"{BATCH_API}/namespaces/{spec.namespace}/jobs/{name}")
        if existing is not None:
            return ExecRef(kind=self.kind, name=name, namespace=spec.namespace, run_id=spec.run_id)
        await self._ensure_secret(spec, name)
        await self.client.request(
            "POST", f"{BATCH_API}/namespaces/{spec.namespace}/jobs", json=self._job(spec, name)
        )
        return ExecRef(kind=self.kind, name=name, namespace=spec.namespace, run_id=spec.run_id)

    async def _ensure_secret(self, spec: StageJobSpec, name: str) -> None:
        existing = await self.client.request("GET", f"{CORE_API}/namespaces/{spec.namespace}/secrets/{name}")
        if existing is not None:
            return
        await self.client.request(
            "POST",
            f"{CORE_API}/namespaces/{spec.namespace}/secrets",
            json={
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": name,
                    "namespace": spec.namespace,
                    "labels": {"choregos/run-id": spec.run_id},
                },
                "type": "Opaque",
                "data": {"token": base64.b64encode(spec.run_token.encode()).decode()},
            },
        )

    def _job(self, spec: StageJobSpec, name: str) -> dict[str, Any]:
        pod_spec: dict[str, Any] = {
            "restartPolicy": "Never",
            "serviceAccountName": self.service_account,
            "automountServiceAccountToken": False,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "fsGroup": 1000},
            "containers": [
                {
                    "name": "runner",
                    "image": spec.runner_image,
                    "args": ["run"],
                    "env": [
                        {"name": "CHOREGOS_RUN_ID", "value": spec.run_id},
                        {"name": "CHOREGOS_API_URL", "value": spec.api_url},
                        {
                            "name": "CHOREGOS_RUN_TOKEN",
                            "valueFrom": {"secretKeyRef": {"name": name, "key": "token"}},
                        },
                        *[{"name": key, "value": value} for key, value in spec.env.items()],
                    ],
                    "resources": {
                        "requests": {"cpu": spec.cpu, "memory": spec.memory},
                        "limits": {"cpu": "2", "memory": "6Gi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}],
                }
            ],
            "volumes": [{"name": "workspace", "emptyDir": {"sizeLimit": "10Gi"}}],
        }
        if spec.runtime_class:
            pod_spec["runtimeClassName"] = spec.runtime_class
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": name,
                "namespace": spec.namespace,
                "labels": {
                    "choregos/run-id": spec.run_id,
                    "choregos/project": spec.project_slug,
                    **spec.labels,
                },
            },
            "spec": {
                "backoffLimit": 0,  # la reprise est décidée par l'orchestrateur, pas par Kubernetes
                "ttlSecondsAfterFinished": self.ttl_seconds,
                "activeDeadlineSeconds": spec.timeout_minutes * 60,
                "template": {
                    # Un label STABLE en plus de l'identifiant du run : les politiques réseau
                    # (Cilium, NetworkPolicy) sélectionnent les runners par famille, pas un à un.
                    "metadata": {"labels": {"choregos/run-id": spec.run_id, **RUNNER_POD_LABELS}},
                    "spec": pod_spec,
                },
            },
        }

    async def status(self, ref: ExecRef) -> ExecStatus:
        payload = await self.client.request("GET", f"{BATCH_API}/namespaces/{ref.namespace}/jobs/{ref.name}")
        if payload is None:
            return ExecStatus(state="unknown", message="Job introuvable")
        status = payload.get("status", {}) or {}
        if status.get("succeeded"):
            state = "succeeded"
        elif status.get("failed"):
            state = "failed"
        elif status.get("active"):
            state = "running"
        else:
            state = "pending"
        return ExecStatus(
            state=state,
            message="; ".join(c.get("message", "") for c in status.get("conditions", []) or [])[:500],
            started_at=_parse_time(status.get("startTime")),
            ended_at=_parse_time(status.get("completionTime")),
        )

    async def logs(self, ref: ExecRef) -> AsyncIterator[str]:
        pods = await self.client.request(
            "GET",
            f"{CORE_API}/namespaces/{ref.namespace}/pods",
            params={"labelSelector": f"choregos/run-id={ref.run_id}"},
        )
        for pod in (pods or {}).get("items", []):
            text = await self.client.request(
                "GET", f"{CORE_API}/namespaces/{ref.namespace}/pods/{pod['metadata']['name']}/log"
            )
            for line in str(text or "").splitlines():
                yield line

    async def cancel(self, ref: ExecRef) -> None:
        await self.client.request(
            "DELETE",
            f"{BATCH_API}/namespaces/{ref.namespace}/jobs/{ref.name}",
            json={"propagationPolicy": "Background"},
        )
        await self.client.request("DELETE", f"{CORE_API}/namespaces/{ref.namespace}/secrets/{ref.name}")

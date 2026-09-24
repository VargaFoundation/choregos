"""Executor Tekton : un `PipelineRun` par étape d'agent (docs/plan/02 §2.3).

Le secret du run est créé pour ce run et supprimé à la fin ; le PipelineRun porte les
labels qui permettent de retrouver le run depuis un événement Tekton.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

import httpx
from choregos_contracts import ExecutorKind, StageResult
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec, utcnow

from ..errors import UpstreamError

TEKTON_API = "/apis/tekton.dev/v1"
CORE_API = "/api/v1"


class KubernetesClient:
    """Client REST Kubernetes minimal : ce dont l'exécuteur a besoin, rien de plus."""

    def __init__(
        self,
        base_url: str = "https://kubernetes.default.svc",
        token: str | None = None,
        *,
        verify: bool | str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token or _in_cluster_token()
        self._client = client or httpx.AsyncClient(timeout=30.0, verify=verify)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        # Les en-têtes de l'appelant s'AJOUTENT aux nôtres. Sans cette fusion, passer
        # `headers=` levait `got multiple values for keyword argument 'headers'` —
        # invisible en relecture, et fatal au premier PATCH, qui doit annoncer son
        # `Content-Type: application/merge-patch+json` sous peine d'être refusé par l'API.
        headers.update(kwargs.pop("headers", None) or {})
        response = await self._client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise UpstreamError(
                "kubernetes",
                f"{method} {path} → {response.status_code} : {response.text[:300]}",
                status_code=response.status_code,
            )
        return response.json() if response.content else {}


def _in_cluster_token() -> str | None:
    from pathlib import Path

    path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


class TektonExecutor:
    """Crée et suit les `PipelineRun` d'étapes d'agent."""

    kind = ExecutorKind.TEKTON

    def __init__(
        self,
        client: KubernetesClient | None = None,
        *,
        pipeline: str = "choregos-agent",
        service_account: str = "choregos-runner",
        storage: str = "10Gi",
    ) -> None:
        self.client = client or KubernetesClient()
        self.pipeline = pipeline
        self.service_account = service_account
        self.storage = storage

    def _name(self, run_id: str) -> str:
        return f"run-{run_id}"[:63].lower()

    async def start(self, spec: StageJobSpec) -> ExecRef:
        """Idempotent : si le PipelineRun existe déjà, on le réutilise."""
        name = self._name(spec.run_id)
        existing = await self.client.request(
            "GET", f"{TEKTON_API}/namespaces/{spec.namespace}/pipelineruns/{name}"
        )
        if existing is not None:
            return ExecRef(kind=self.kind, name=name, namespace=spec.namespace, run_id=spec.run_id)

        await self._ensure_secret(spec, name)
        body = self._pipeline_run(spec, name)
        await self.client.request("POST", f"{TEKTON_API}/namespaces/{spec.namespace}/pipelineruns", json=body)
        return ExecRef(
            kind=self.kind,
            name=name,
            namespace=spec.namespace,
            run_id=spec.run_id,
            url=f"/#/namespaces/{spec.namespace}/pipelineruns/{name}",
        )

    async def _ensure_secret(self, spec: StageJobSpec, name: str) -> None:
        secret = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": name,
                "namespace": spec.namespace,
                "labels": {"choregos/run-id": spec.run_id, "choregos/project": spec.project_slug},
            },
            "type": "Opaque",
            "data": {"token": base64.b64encode(spec.run_token.encode()).decode()},
        }
        existing = await self.client.request("GET", f"{CORE_API}/namespaces/{spec.namespace}/secrets/{name}")
        if existing is None:
            await self.client.request("POST", f"{CORE_API}/namespaces/{spec.namespace}/secrets", json=secret)

    def _pipeline_run(self, spec: StageJobSpec, name: str) -> dict[str, Any]:
        pod_template: dict[str, Any] = {
            "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "fsGroup": 1000},
            "nodeSelector": {"role": "runners"},
            "tolerations": [{"key": "role", "operator": "Equal", "value": "runners", "effect": "NoSchedule"}],
        }
        if spec.runtime_class:
            pod_template["runtimeClassName"] = spec.runtime_class
        return {
            "apiVersion": "tekton.dev/v1",
            "kind": "PipelineRun",
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
                "pipelineRef": {"name": self.pipeline},
                "taskRunTemplate": {"serviceAccountName": self.service_account, "podTemplate": pod_template},
                "timeouts": {"pipeline": f"{spec.timeout_minutes}m"},
                "params": [
                    {"name": "run-id", "value": spec.run_id},
                    {"name": "api-url", "value": spec.api_url},
                    {"name": "runner-image", "value": spec.runner_image},
                    {
                        "name": "repo-url",
                        "value": (spec.stage_input.repo.url if spec.stage_input else ""),
                    },
                    {
                        "name": "revision",
                        "value": (spec.stage_input.repo.base_branch if spec.stage_input else "main"),
                    },
                ],
                "workspaces": [
                    {
                        "name": "source",
                        "volumeClaimTemplate": {
                            "spec": {
                                "accessModes": ["ReadWriteOnce"],
                                "resources": {"requests": {"storage": self.storage}},
                            }
                        },
                    }
                ],
            },
        }

    async def status(self, ref: ExecRef) -> ExecStatus:
        payload = await self.client.request(
            "GET", f"{TEKTON_API}/namespaces/{ref.namespace}/pipelineruns/{ref.name}"
        )
        if payload is None:
            return ExecStatus(state="unknown", message="PipelineRun introuvable")
        status = payload.get("status", {}) or {}
        conditions = status.get("conditions", []) or []
        condition = conditions[0] if conditions else {}
        succeeded = str(condition.get("status", "Unknown"))
        reason = str(condition.get("reason", ""))
        state = {
            ("True", ""): "succeeded",
            ("False", ""): "failed",
            ("Unknown", ""): "running",
        }.get((succeeded, ""), "running")
        if reason in {"Cancelled", "PipelineRunCancelled"}:
            state = "cancelled"
        results = {
            str(item.get("name")): str(item.get("value", "")) for item in status.get("results", []) or []
        }
        return ExecStatus(
            state=state,
            message=str(condition.get("message", ""))[:500],
            result_url=results.get("result-url"),
            started_at=_parse_time(status.get("startTime")),
            ended_at=_parse_time(status.get("completionTime")),
        )

    async def fetch_result(self, ref: ExecRef) -> StageResult | None:
        """Lit le `StageResult` publié par le runner via les résultats Tekton, si besoin."""
        status = await self.status(ref)
        if not status.result_url or not status.result_url.startswith("http"):
            return None
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(status.result_url)
            if response.status_code >= 400:
                return None
            return StageResult.model_validate(response.json())

    async def logs(self, ref: ExecRef) -> AsyncIterator[str]:
        pods = await self.client.request(
            "GET",
            f"{CORE_API}/namespaces/{ref.namespace}/pods",
            params={"labelSelector": f"tekton.dev/pipelineRun={ref.name}"},
        )
        for pod in (pods or {}).get("items", []):
            name = pod["metadata"]["name"]
            text = await self.client.request(
                "GET",
                f"{CORE_API}/namespaces/{ref.namespace}/pods/{name}/log",
                params={"container": "step-run"},
            )
            for line in str(text or "").splitlines():
                yield line

    async def cancel(self, ref: ExecRef) -> None:
        """Annulation propre : Tekton arrête les Task, puis on nettoie le secret."""
        await self.client.request(
            "PATCH",
            f"{TEKTON_API}/namespaces/{ref.namespace}/pipelineruns/{ref.name}",
            json={"spec": {"status": "Cancelled"}},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        await self.cleanup(ref)

    async def cleanup(self, ref: ExecRef) -> None:
        await self.client.request("DELETE", f"{CORE_API}/namespaces/{ref.namespace}/secrets/{ref.name}")

    async def test(self) -> dict[str, Any]:
        payload = await self.client.request("GET", f"{TEKTON_API}")
        return {"ok": payload is not None}


def _parse_time(value: Any) -> Any:
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return utcnow()

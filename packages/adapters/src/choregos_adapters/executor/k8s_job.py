"""Executor Job Kubernetes : le repli quand Tekton n'est pas disponible (§2.3).

Même contrat que Tekton : un Job par run, un Secret pour le jeton, un nettoyage automatique
par `ttlSecondsAfterFinished`.

DENSITÉ — un Job par étape, sans plafond, met le cluster à la merci d'une pointe : dix
tickets qui démarrent ensemble font dix pods qui tirent la même image en même temps, et un
quota de namespace qui refuse le onzième sans que rien ne l'explique. La réponse ici est
celle de Kubernetes lui-même : `spec.suspend`. Un Job créé **suspendu** n'a pas de pod ; il
attend son tour, visible, ordonné, sans rien consommer. L'admission se fait au fil des
relevés d'état — l'orchestrateur interroge déjà chaque run — et dans l'ordre d'arrivée.

Ce n'est pas un ordonnanceur : le plafond est SOUPLE. Deux runs peuvent s'admettre dans la
même fenêtre et dépasser d'un. C'est le prix à payer pour n'ajouter aucun composant, et il
est sans commune mesure avec le problème qu'on évite.
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
        cpu_limit: str = "2",
        memory_limit: str = "6Gi",
        env_from_secrets: list[str] | None = None,
        max_active: int = 0,
    ) -> None:
        self.client = client or KubernetesClient()
        self.service_account = service_account
        self.ttl_seconds = ttl_seconds
        # Réglables : un namespace sous LimitRange refuse le pod entier au-delà de son
        # plafond par conteneur (4 Gi chez un locataire Diametral), sans dire pourquoi au Job.
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        # Secrets injectés EN ENTIER dans le pod d'agent (`envFrom`) : identifiants git,
        # jeton d'un backend. Par référence, jamais par valeur — une valeur recopiée ici
        # se retrouverait dans la spec du Job, lisible par qui peut lire un pod.
        self.env_from_secrets = list(env_from_secrets or [])
        # Nombre de runs qui peuvent tourner EN MÊME TEMPS dans un namespace. 0 = sans
        # plafond, le comportement d'avant : un déploiement qui ne dit rien ne change pas
        # de régime du jour au lendemain.
        self.max_active = max(0, int(max_active))

    def _name(self, run_id: str) -> str:
        return f"run-{run_id}"[:63].lower()

    async def start(self, spec: StageJobSpec) -> ExecRef:
        name = self._name(spec.run_id)
        existing = await self.client.request("GET", f"{BATCH_API}/namespaces/{spec.namespace}/jobs/{name}")
        if existing is not None:
            return ExecRef(kind=self.kind, name=name, namespace=spec.namespace, run_id=spec.run_id)
        await self._ensure_secret(spec, name)
        job = self._job(spec, name)
        if self.max_active:
            actifs, _ = await self._file(spec.namespace)
            if actifs >= self.max_active:
                # Suspendu : aucun pod, aucune image tirée, aucune place prise dans le
                # quota. Le tour viendra à un relevé d'état, dans l'ordre d'arrivée.
                job["spec"]["suspend"] = True
        await self.client.request("POST", f"{BATCH_API}/namespaces/{spec.namespace}/jobs", json=job)
        return ExecRef(kind=self.kind, name=name, namespace=spec.namespace, run_id=spec.run_id)

    async def _file(self, namespace: str) -> tuple[int, list[dict[str, Any]]]:
        """Combien de runs tournent, et quels Jobs attendent — les plus anciens d'abord."""
        selecteur = "choregos/run-id"
        payload = await self.client.request(
            "GET", f"{BATCH_API}/namespaces/{namespace}/jobs", params={"labelSelector": selecteur}
        )
        actifs = 0
        suspendus: list[dict[str, Any]] = []
        for item in (payload or {}).get("items", []):
            statut = item.get("status", {}) or {}
            if statut.get("succeeded") or statut.get("failed"):
                continue
            if (item.get("spec", {}) or {}).get("suspend"):
                suspendus.append(item)
            else:
                actifs += 1
        suspendus.sort(key=lambda j: str(j.get("metadata", {}).get("creationTimestamp", "")))
        return actifs, suspendus

    async def _admettre(self, ref: ExecRef, job: dict[str, Any]) -> bool:
        """Réveille ce Job si une place s'est libérée ET qu'il est le prochain sur la liste.

        L'ordre d'arrivée compte : sans lui, le dernier ticket posé passerait devant les
        autres à chaque relevé, et un run malchanceux attendrait indéfiniment.
        """
        namespace = ref.namespace or ""
        actifs, suspendus = await self._file(namespace)
        places = self.max_active - actifs
        if places <= 0:
            return False
        prochains = {j.get("metadata", {}).get("name") for j in suspendus[:places]}
        if ref.name not in prochains:
            return False
        await self.client.request(
            "PATCH",
            f"{BATCH_API}/namespaces/{namespace}/jobs/{ref.name}",
            json={"spec": {"suspend": False}},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        return True

    async def _ensure_secret(self, spec: StageJobSpec, name: str) -> None:
        """Écrit le jeton du run, MÊME si un secret de ce nom existe déjà.

        Le nom est déterministe (il vaut celui du Job), donc un run rejoué retombe sur le
        secret d'avant. Le laisser tel quel faisait tourner l'agent avec un jeton périmé :
        l'API répondait « Signature verification failed », et le message accuse la
        signature — jamais le secret qu'on a cru inutile de réécrire.
        """
        existing = await self.client.request("GET", f"{CORE_API}/namespaces/{spec.namespace}/secrets/{name}")
        if existing is not None:
            await self.client.request(
                "PUT",
                f"{CORE_API}/namespaces/{spec.namespace}/secrets/{name}",
                json=self._secret(spec, name),
            )
            return
        await self.client.request(
            "POST",
            f"{CORE_API}/namespaces/{spec.namespace}/secrets",
            json=self._secret(spec, name),
        )

    def _secret(self, spec: StageJobSpec, name: str) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": name,
                "namespace": spec.namespace,
                "labels": {"choregos/run-id": spec.run_id},
            },
            "type": "Opaque",
            "data": {"token": base64.b64encode(spec.run_token.encode()).decode()},
        }

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
                        "limits": {"cpu": self.cpu_limit, "memory": self.memory_limit},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    **(
                        {"envFrom": [{"secretRef": {"name": name}} for name in self.env_from_secrets]}
                        if self.env_from_secrets
                        else {}
                    ),
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
        attente = ""
        if state == "pending" and (payload.get("spec", {}) or {}).get("suspend"):
            # C'est ici que la file avance : l'orchestrateur relève déjà l'état de chaque
            # run, donc chaque tour de boucle est une occasion d'admettre le suivant. Pas
            # de composant en plus, pas de minuterie à régler.
            admis = await self._admettre(ref, payload) if self.max_active else False
            attente = "" if admis else f"en attente d'une place (plafond {self.max_active} par namespace)"
        return ExecStatus(
            state=state,
            message=attente
            or "; ".join(c.get("message", "") for c in status.get("conditions", []) or [])[:500],
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

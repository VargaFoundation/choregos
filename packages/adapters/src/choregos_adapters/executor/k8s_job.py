# SPDX-License-Identifier: Apache-2.0
"""The Kubernetes Job executor: the fallback when Tekton is not available (§2.3).

The same contract as Tekton: one Job per run, one Secret for the token (attached to the Job, so
collected with it), an automatic cleanup through `ttlSecondsAfterFinished`.

DENSITY: one Job per step, with no ceiling, leaves the cluster at the mercy of a spike: ten
tickets that start together make ten pods pulling the same image at the same time, and a
namespace quota that refuses the eleventh with nothing to explain it. The answer here is
Kubernetes' own: `spec.suspend`. A Job created **suspended** has no pod; it waits its turn,
visible, ordered, consuming nothing. Admission happens as the state polls come in (the
orchestrator already polls every run), and in arrival order.

This is not a scheduler: the ceiling is SOFT. Two runs can be admitted in the same window and
exceed it by one. That is the price to pay for adding no component, and it is out of all
proportion with the problem it avoids.
"""

from __future__ import annotations

import base64
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from choregos_contracts import ExecutorKind
from choregos_core.domain import (
    RUN_GATEWAY_KEY_ENV,
    RUN_REMOTE_TOKEN_ENV,
    AgentContainer,
    ExecRef,
    ExecStatus,
    StageJobSpec,
)
from choregos_core.exit_codes import explanation
from choregos_core.hosted import SHARED_WORKSPACE_AGENT, SHARED_WORKSPACE_RUNNER, native_sidecars_refusal

from ..errors import UnsupportedClusterError
from .tekton import (
    CORE_API,
    GATEWAY_KEY_FIELD,
    REMOTE_TOKEN_FIELD,
    KubernetesClient,
    _parse_time,
    run_secret_data,
)

BATCH_API = "/apis/batch/v1"
#: The name of a hosted agent's container in the run's pod (ADR 0080).
AGENT_CONTAINER = "agent"
#: The volume a hosted container shares with the runner, the copy of the working tree (S41-18).
HOSTED_WORKSPACE_VOLUME = "hosted-workspace"
RUNNER_POD_LABELS = {"app.kubernetes.io/name": "choregos-runner", "app.kubernetes.io/component": "runner"}


class KubernetesJobExecutor:
    kind = ExecutorKind.K8S_JOB
    # `queue` only: Kubernetes cannot suspend a Job that has ALREADY started without destroying
    # its pod, so neither `suspend` nor `snapshot` here. Saying so rather than letting it be
    # assumed is what will allow plugging in another one without breaking anything.
    capabilities = frozenset({"queue", "renew"})

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
        image_pull_secrets: list[str] | None = None,
    ) -> None:
        self.client = client or KubernetesClient()
        self.service_account = service_account
        self.ttl_seconds = ttl_seconds
        # Adjustable: a namespace under a LimitRange refuses the whole pod beyond its per-container
        # ceiling (4 Gi at one operator's tenants), without telling the Job why.
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        # Secrets injected WHOLE into the agent pod (`envFrom`): git credentials, a backend's
        # token. By reference, never by value: a value copied here would end up in the Job's
        # spec, readable by whoever can read a pod.
        self.env_from_secrets = list(env_from_secrets or [])
        # The number of runs that may run AT THE SAME TIME in a namespace. 0 = no ceiling, the
        # former behaviour: a deployment that says nothing does not change regime overnight.
        self.max_active = max(0, int(max_active))
        #: The pull secrets of the runner's pod (S51-09), by name: a job step's named image, or a
        #: hosted agent's, may sit in a private registry project the runner image is not in.
        self.image_pull_secrets = [name for name in (image_pull_secrets or []) if name]
        #: The API server's `/version`, read once: a cluster is not upgraded under a running worker
        #: often enough to read it at every start (S41-16).
        self._server_version: dict[str, Any] | None = None

    async def server_version(self) -> dict[str, Any]:
        """The API server's `/version` body, read at the first call and kept. A failed read raises
        (an `UpstreamError`, retryable) and is not kept."""
        if self._server_version is None:
            found = await self.client.request("GET", "/version")
            self._server_version = found if isinstance(found, dict) else {}
        return self._server_version

    async def _require_native_sidecars(self) -> None:
        refusal = native_sidecars_refusal(await self.server_version())
        if refusal is not None:
            raise UnsupportedClusterError(refusal)

    def _name(self, run_id: str) -> str:
        return f"run-{run_id}"[:63].lower()

    async def start(self, spec: StageJobSpec) -> ExecRef:
        if spec.agent_container is not None:
            # Below 1.29 the API server drops `restartPolicy: Always` from the init container: the
            # pod would wait on the agent forever. Refused before anything is created.
            await self._require_native_sidecars()
        name = self._name(spec.run_id)
        existing = await self.client.request("GET", f"{BATCH_API}/namespaces/{spec.namespace}/jobs/{name}")
        if existing is not None:
            # A REPLAYED start (the activity failed after the Job was created): what failed
            # may have been handing the Secret to the Job. Seen on the dev tenant on
            # 2026-09-27 — `PATCH .../secrets/run-... -> 403`, the activity retried, found the
            # Job, returned... and the Secret stayed ownerless for good. It is handed over here.
            await self._attach_secret(spec.namespace, name, existing)
            return ExecRef(kind=self.kind, name=name, namespace=spec.namespace, run_id=spec.run_id)
        await self._ensure_secret(spec, name)
        job = self._job(spec, name)
        if self.max_active:
            active, _ = await self._queue_state(spec.namespace)
            if active >= self.max_active:
                # Suspended: no pod, no image pulled, no place taken in the quota. Its turn will
                # come at a state poll, in arrival order.
                job["spec"]["suspend"] = True
        try:
            created = await self.client.request(
                "POST", f"{BATCH_API}/namespaces/{spec.namespace}/jobs", json=job
            )
        except Exception:
            await self._drop_secret_without_job(spec.namespace, name)
            raise
        await self._attach_secret(spec.namespace, name, created)
        return ExecRef(kind=self.kind, name=name, namespace=spec.namespace, run_id=spec.run_id)

    async def _attach_secret(self, namespace: str, name: str, job: Any) -> None:
        """The run's secret belongs to its Job: when `ttlSecondsAfterFinished` takes the Job away,
        the garbage collector takes the token with it.

        Without this link, every run left its secret behind: sixty-four tokens of finished runs in
        the bench namespace, valid until they expire, and a list that only grows. `cancel` deletes
        explicitly; here it is the normal end that cleans up. Without a `uid` (a client that does
        not return the created object), nothing breaks: the secret stays orphaned, as before.
        """
        uid = ((job or {}).get("metadata") or {}).get("uid") if isinstance(job, dict) else None
        if not uid:
            return
        await self.client.request(
            "PATCH",
            f"{CORE_API}/namespaces/{namespace}/secrets/{name}",
            json={
                "metadata": {
                    "ownerReferences": [{"apiVersion": "batch/v1", "kind": "Job", "name": name, "uid": uid}]
                }
            },
            headers={"Content-Type": "application/merge-patch+json"},
        )

    async def _drop_secret_without_job(self, namespace: str, name: str) -> None:
        """The Job could not be created: the Secret written just before it has no owner.

        Deleted only if the Job really does not exist — a lost answer after a successful
        create would otherwise leave a pod without its token. In doubt (the API does not
        answer), it is left: the replayed start hands it to the Job or rewrites it.
        """
        with contextlib.suppress(Exception):  # the original error is the one to raise
            if await self.client.request("GET", f"{BATCH_API}/namespaces/{namespace}/jobs/{name}") is None:
                await self.client.request("DELETE", f"{CORE_API}/namespaces/{namespace}/secrets/{name}")

    async def _queue_state(self, namespace: str) -> tuple[int, list[dict[str, Any]]]:
        """How many runs are running, and which Jobs are waiting, the oldest first."""
        selector = "choregos/run-id"
        payload = await self.client.request(
            "GET", f"{BATCH_API}/namespaces/{namespace}/jobs", params={"labelSelector": selector}
        )
        active = 0
        suspended: list[dict[str, Any]] = []
        for item in (payload or {}).get("items", []):
            job_status = item.get("status", {}) or {}
            if job_status.get("succeeded") or job_status.get("failed"):
                continue
            if (item.get("spec", {}) or {}).get("suspend"):
                suspended.append(item)
            else:
                active += 1
        suspended.sort(key=lambda j: str(j.get("metadata", {}).get("creationTimestamp", "")))
        return active, suspended

    async def _admit(self, ref: ExecRef, job: dict[str, Any]) -> bool:
        """Wakes this Job if a place has freed up AND it is next on the list.

        Arrival order matters: without it, the last ticket laid down would go ahead of the others
        at every poll, and an unlucky run would wait indefinitely.
        """
        namespace = ref.namespace or ""
        active, suspended = await self._queue_state(namespace)
        places = self.max_active - active
        if places <= 0:
            return False
        next_up = {j.get("metadata", {}).get("name") for j in suspended[:places]}
        if ref.name not in next_up:
            return False
        try:
            await self.client.request(
                "PATCH",
                f"{BATCH_API}/namespaces/{namespace}/jobs/{ref.name}",
                json={"spec": {"suspend": False}},
                headers={"Content-Type": "application/merge-patch+json"},
            )
        except Exception:
            # A failed admission is NOT the end of the run: it stays queued, and the next poll
            # will try again. Without this net, a missing right (`patch` absent from the Role)
            # made the activity fail and the ticket's workflow die: three tickets lost on the
            # bench of 2026-09-24, one of which expired without having run.
            # The lack still shows: the run stays "waiting for a slot".
            return False
        return True

    async def _ensure_secret(self, spec: StageJobSpec, name: str) -> None:
        """Writes the run's token, EVEN if a secret of that name already exists.

        The name is deterministic (it is the Job's), so a replayed run lands on the earlier
        secret. Leaving it as it was made the agent run with a stale token: the API answered
        "Signature verification failed", and the message blames the signature, never the secret
        that was thought not worth rewriting.
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
            "data": run_secret_data(spec),
        }

    def _job(self, spec: StageJobSpec, name: str) -> dict[str, Any]:
        pod_spec: dict[str, Any] = {
            "restartPolicy": "Never",
            "serviceAccountName": self.service_account,
            "automountServiceAccountToken": False,
            # The POD's context, not only the container's: the strict Kyverno rule
            # (`infra/policies/pod-security.yaml`) requires the seccomp profile at this level.
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "fsGroup": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "runner",
                    "image": spec.runner_image,
                    "args": list(spec.runner_command),
                    "env": [
                        {"name": "CHOREGOS_RUN_ID", "value": spec.run_id},
                        {"name": "CHOREGOS_API_URL", "value": spec.api_url},
                        {
                            "name": "CHOREGOS_RUN_TOKEN",
                            "valueFrom": {"secretKeyRef": {"name": name, "key": "token"}},
                        },
                        # The run's gateway key, from the same Secret (#523). Optional: a run
                        # without a key (none minted) still starts.
                        {
                            "name": RUN_GATEWAY_KEY_ENV,
                            "valueFrom": {
                                "secretKeyRef": {"name": name, "key": GATEWAY_KEY_FIELD, "optional": True}
                            },
                        },
                        # The access token of a remote agent (ADR 0062), same Secret, same rule.
                        {
                            "name": RUN_REMOTE_TOKEN_ENV,
                            "valueFrom": {
                                "secretKeyRef": {"name": name, "key": REMOTE_TOKEN_FIELD, "optional": True}
                            },
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
        if self.image_pull_secrets:
            pod_spec["imagePullSecrets"] = [{"name": name} for name in self.image_pull_secrets]
        if spec.agent_container is not None:
            self._add_agent_container(pod_spec, spec.agent_container)
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
                "backoffLimit": 0,  # the retry is decided by the orchestrator, not by Kubernetes
                "ttlSecondsAfterFinished": self.ttl_seconds,
                "activeDeadlineSeconds": spec.timeout_minutes * 60,
                "template": {
                    # A STABLE label besides the run's identifier: the network policies
                    # (Cilium, NetworkPolicy) select runners by family, not one by one.
                    "metadata": {"labels": {"choregos/run-id": spec.run_id, **RUNNER_POD_LABELS}},
                    "spec": pod_spec,
                },
            },
        }

    @staticmethod
    def _add_agent_container(pod_spec: dict[str, Any], agent: AgentContainer) -> None:
        """A hosted agent's container (ADR 0080), as a native sidecar (Kubernetes >= 1.29): an init
        container with `restartPolicy: Always` starts before the runner, shares its loopback, and is
        stopped when the runner ends, so the Job completes on the runner's exit alone.

        It gets plain values only: no `secretKeyRef`, no `envFrom`, no volume of the runner's. The
        runner's token, gateway key and MCP credentials stay in the runner container. A version that
        asks for the workspace (S41-18) gets one more volume, which the runner fills with a copy of
        the working tree: the clone and its `.git` stay in the runner's `workspace` volume."""
        mounts: list[dict[str, Any]] = []
        if agent.workspace != "none":
            pod_spec["volumes"].append({"name": HOSTED_WORKSPACE_VOLUME, "emptyDir": {"sizeLimit": "10Gi"}})
            runner = next(c for c in pod_spec["containers"] if c["name"] == "runner")
            runner["volumeMounts"].append(
                {"name": HOSTED_WORKSPACE_VOLUME, "mountPath": SHARED_WORKSPACE_RUNNER}
            )
            mounts.append(
                {
                    "name": HOSTED_WORKSPACE_VOLUME,
                    "mountPath": SHARED_WORKSPACE_AGENT,
                    "readOnly": agent.workspace == "read",
                }
            )
        pod_spec["initContainers"] = [
            {
                "name": AGENT_CONTAINER,
                "image": agent.image,
                "restartPolicy": "Always",
                "env": [{"name": key, "value": value} for key, value in sorted(agent.env.items())],
                "ports": [{"name": "agent", "containerPort": agent.port}],
                **({"volumeMounts": mounts} if mounts else {}),
                "resources": {
                    "requests": {"cpu": agent.cpu, "memory": agent.memory},
                    "limits": {"cpu": agent.cpu, "memory": agent.memory},
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                    "runAsNonRoot": True,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
            }
        ]
        # The cluster's services are not announced to the container as variables.
        pod_spec["enableServiceLinks"] = False
        pod_spec["terminationGracePeriodSeconds"] = agent.termination_grace_s

    async def renew(self, ref: ExecRef, run_token: str) -> None:
        """Replaces the token of a run that has not started yet.

        A token is minted when the step is prepared, and lives `max_minutes + 15`. With a queue,
        this countdown runs DURING the wait: a run admitted later starts with a stale token and
        receives "Signature has expired" on its first call, as seen on the bench of 2026-09-24.
        The secret has the same name as the Job, so replacing it is enough: the pod will read it
        when it starts.

        A merge PATCH of the `token` key alone, never a PUT of the whole object: a PUT
        replaces the metadata, and a Secret rewritten without `ownerReferences` no longer
        belongs to its Job. That is how queued runs left their token behind (three orphan
        Secrets on the dev tenant on 2026-10-08, all from runs that waited for a slot).
        No Secret (already collected): nothing to renew.
        """
        namespace = ref.namespace or ""
        # A merge patch of the token alone: the gateway key next to it stays (#523). A `PUT` of a
        # token-only Secret would have started the run without its key.
        await self.client.request(
            "PATCH",
            f"{CORE_API}/namespaces/{namespace}/secrets/{ref.name}",
            json={"data": {"token": base64.b64encode(run_token.encode()).decode()}},
            headers={"Content-Type": "application/merge-patch+json"},
        )

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
        waiting_text = ""
        if state == "pending" and (payload.get("spec", {}) or {}).get("suspend"):
            # This is where the queue advances: the orchestrator already polls every run's state,
            # so each pass of the loop is a chance to admit the next one. No extra component, no
            # timer to tune.
            admitted = await self._admit(ref, payload) if self.max_active else False
            waiting_text = "" if admitted else f"waiting for a slot (cap {self.max_active} per namespace)"
        message = (
            waiting_text or "; ".join(c.get("message", "") for c in status.get("conditions", []) or [])[:500]
        )
        code = None
        reason: str | None = None
        if state == "failed":
            # "Job has reached the specified backoff limit" does not say why: the pod knows, through
            # its exit code, which the runner chooses (`choregos_core.exit_codes`), or the reason its
            # container never started (image, secret, configuration).
            code, cause = await self._diagnostic(ref)
            reason = await self._termination_reason(ref)
            if cause:
                message = f"{cause} — {message}" if message else cause
        return ExecStatus(
            state=state,
            exit_code=code,
            reason=reason,
            message=message[:700],
            started_at=_parse_time(status.get("startTime")),
            ended_at=_parse_time(status.get("completionTime")),
        )

    async def _diagnostic(self, ref: ExecRef) -> tuple[int | None, str]:
        """The runner container's exit code and what it says, read on the run's LAST pod; or why
        that container is still waiting. Never the logs: they can contain anything."""
        pods = await self.client.request(
            "GET",
            f"{CORE_API}/namespaces/{ref.namespace}/pods",
            params={"labelSelector": f"choregos/run-id={ref.run_id}"},
        )
        items = sorted(
            (pods or {}).get("items", []) or [],
            key=lambda pod: str((pod.get("metadata") or {}).get("creationTimestamp") or ""),
        )
        if not items:
            return None, "no pod for the run: refused at admission, or already deleted"
        states = (items[-1].get("status") or {}).get("containerStatuses") or []
        container = next((c for c in states if c.get("name") == "runner"), states[0] if states else None)
        if container is None:
            return None, ""
        current = container.get("state") or {}
        previous = container.get("lastState") or {}
        terminated = current.get("terminated") or previous.get("terminated")
        if terminated:
            code = terminated.get("exitCode")
            reason = str(terminated.get("reason") or "terminated")
            meaning = explanation(code)
            return code, f"runner exited with {code} ({reason}{': ' + meaning if meaning else ''})"
        waiting = current.get("waiting")
        if waiting:
            detail = str(waiting.get("message") or "")[:200]
            return None, f"runner never started: {waiting.get('reason')}{' - ' + detail if detail else ''}"
        return None, ""

    async def _termination_reason(self, ref: ExecRef) -> str | None:
        """The runtime's own word for how the runner container ended, or why it never started
        (`OOMKilled`, `Error`, `ErrImagePull`, `ImagePullBackOff`…), read on the run's last pod."""
        pods = await self.client.request(
            "GET",
            f"{CORE_API}/namespaces/{ref.namespace}/pods",
            params={"labelSelector": f"choregos/run-id={ref.run_id}"},
        )
        items = sorted(
            (pods or {}).get("items", []) or [],
            key=lambda pod: str((pod.get("metadata") or {}).get("creationTimestamp") or ""),
        )
        if not items:
            return None
        statuses = (items[-1].get("status") or {}).get("containerStatuses") or []
        container = next(
            (c for c in statuses if c.get("name") == "runner"), statuses[0] if statuses else None
        )
        if container is None:
            return None
        state = container.get("state") or {}
        ended = state.get("terminated") or (container.get("lastState") or {}).get("terminated")
        if ended:
            return str(ended.get("reason") or "") or None
        waiting = state.get("waiting") or {}
        return str(waiting.get("reason") or "") or None

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

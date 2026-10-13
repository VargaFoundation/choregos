# SPDX-License-Identifier: Apache-2.0
"""Executor on `kubernetes-sigs/agent-sandbox` (ADR 0016, ADR 0053): one SandboxClaim per run.

The chart renders a SandboxTemplate and a SandboxWarmPool (`charts/.../sandbox.yaml`). A warm
sandbox is a pod made from the template that has NEVER run anything: no run token, no git
identity, no backend key, no service account token. Its runner listens on one port for one
assignment. A run goes like this:

1. `start` writes the run's Secret (its token, by reference, and what the assignment needs) and
   creates a SandboxClaim on the warm pool. The controller binds a warm sandbox to it, or
   cold-starts one from the same template when the pool is empty: either way a clean sandbox,
   never one a previous run has touched (`snapshot`, in ADR 0016's sense).
2. `status`, once the bound sandbox is Ready, pushes the assignment — run id, token,
   environment — to THAT pod, SEALED to the key the pod printed at boot (read through the API
   server), and marks the claim assigned. Before this push the token exists only in the run's
   Secret, which no runner pod can read; after it, exactly one pod holds it, and the pod network
   only ever carried ciphertext.
3. The runner runs the stage and exits; the claim reports the pod's end, and is deleted
   `ttl_seconds` later by the controller, taking the sandbox, its volume and (through the owner
   reference) the run's Secret with it. A sandbox is never handed out twice.

`queue`: agent-sandbox v1.0.6 cold-starts a claim when the pool is empty — a claim never
waits. So the waiting is ours, the discipline of ADR 0013: beyond `max_active` runs in the
namespace, a run waits as its Secret alone (no claim, no pod, no image pull), and is admitted in
arrival order along status polls. The cap is soft, as for `k8s_job`.

`renew` replaces the token of a run not yet assigned (its Secret); once pushed, the pod has it.

`suspend`/`resume` (S35-03): around a person's answer to its agent's question, the sandbox is
suspended — the pod goes, the workspace volume stays — and resumed for the SAME run, which is
pushed again to the new pod with a fresh token. The claim's `shutdownTime` holds the policy's
bound (`sandbox.suspend_max_hours`) while it waits: past it the controller deletes everything,
and the orchestrator replays the stage as it would with any other executor.

The API is PINNED: `agents.x-k8s.io/v1beta1` and `extensions.agents.x-k8s.io/v1beta1`, as served
by the release in `infra/operators/agent-sandbox/`. What the executor writes is validated
against that release's CRD schemas in the tests (`tests/fixtures/agent_sandbox/`).
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec, utcnow
from choregos_core.sorties import explication

from ..errors import ConfigurationError, UpstreamError
from ..sealing import key_from_log, seal
from .tekton import CORE_API, KubernetesClient, _parse_time

#: The operator release whose API this executor speaks. Upgrading it is a change here AND in
#: `infra/operators/agent-sandbox/`, proved by `make test-cluster` on a cluster that has it.
AGENT_SANDBOX_RELEASE = "v1.0.6"
SANDBOX_API = "/apis/agents.x-k8s.io/v1beta1"
EXTENSIONS_API = "/apis/extensions.agents.x-k8s.io/v1beta1"
#: Set on the claim once its run has been pushed to the sandbox: the push happens once.
ASSIGNED = "choregos/assigned-at"
#: On the run's Secret while the run waits for a slot (`queue`), before any claim exists.
QUEUED = "choregos/queued"
#: Where the controller records the name of a sandbox's pod when it is not the sandbox's own.
POD_NAME_ANNOTATION = "agents.x-k8s.io/pod-name"


class AgentSandboxExecutor:
    kind = ExecutorKind.AGENT_SANDBOX
    capabilities: frozenset[str] = frozenset({"queue", "renew", "snapshot", "suspend", "resume"})

    def __init__(
        self,
        client: KubernetesClient | None = None,
        *,
        warm_pool: str = "choregos-runner",
        assignment_port: int = 8765,
        runtime_class: str | None = None,
        ttl_seconds: int = 3600,
        max_active: int = 0,
        env_from_secrets: list[str] | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.client = client or KubernetesClient()
        self.warm_pool = warm_pool
        self.assignment_port = int(assignment_port)
        # The template's RuntimeClass. A claim cannot choose another one: the pool's pods are
        # already made. A run whose policy asks for a different one is refused, not run.
        self.runtime_class = runtime_class or None
        self.ttl_seconds = int(ttl_seconds)
        self.max_active = max(0, int(max_active))
        # Deployment-wide Secrets (git identity, backend key) a `k8s_job` pod mounts with
        # `envFrom`. A warm pod must not hold them: they are read and pushed WITH the assignment.
        self.env_from_secrets = list(env_from_secrets or [])
        self.http = http or httpx.AsyncClient(timeout=10.0)

    def _name(self, run_id: str) -> str:
        return f"run-{run_id}"[:63].lower()

    def _ref(self, name: str, namespace: str, run_id: str) -> ExecRef:
        return ExecRef(kind=self.kind, name=name, namespace=namespace, run_id=run_id)

    def _claims(self, namespace: str) -> str:
        return f"{EXTENSIONS_API}/namespaces/{namespace}/sandboxclaims"

    def _secrets(self, namespace: str) -> str:
        return f"{CORE_API}/namespaces/{namespace}/secrets"

    # ───────────────────────────── start ─────────────────────────────

    async def start(self, spec: StageJobSpec) -> ExecRef:
        """Idempotent by `run_id`: a second start finds the claim, or the run still queued."""
        if spec.runtime_class and spec.runtime_class != self.runtime_class:
            raise ConfigurationError(
                f"the run asks for RuntimeClass `{spec.runtime_class}`, the warm pool "
                f"`{self.warm_pool}` runs `{self.runtime_class or 'the cluster default'}`: a sandbox "
                "cannot change runtime once made; set `runner.runtimeClass` to match the policy"
            )
        name = self._name(spec.run_id)
        ref = self._ref(name, spec.namespace, spec.run_id)
        if await self.client.request("GET", f"{self._claims(spec.namespace)}/{name}") is not None:
            return ref
        existing = await self.client.request("GET", f"{self._secrets(spec.namespace)}/{name}")
        if _labels(existing).get(QUEUED) == "true":
            return ref
        await self._write_secret(spec, name, existing)
        if self.max_active:
            active, _ = await self._queue(spec.namespace)
            if active >= self.max_active:
                return ref  # queued: the Secret alone, no claim, no pod
        await self._claim(spec.namespace, name)
        return ref

    async def _write_secret(self, spec: StageJobSpec, name: str, existing: Any) -> None:
        """The run's token, and what its assignment needs, written even if a Secret of this
        name exists: a replayed run must not start with the token of the run before."""
        assignment = {
            "run_id": spec.run_id,
            "api_url": spec.api_url,
            "env": dict(spec.env),
            "timeout_minutes": spec.timeout_minutes,
            "labels": {"choregos/run-id": spec.run_id, "choregos/project": spec.project_slug, **spec.labels},
        }
        queued = "true" if self.max_active else "false"
        secret = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": name,
                "namespace": spec.namespace,
                "labels": {"choregos/run-id": spec.run_id, QUEUED: queued},
            },
            "type": "Opaque",
            "data": {
                "token": _b64(spec.run_token),
                "assignment": _b64(json.dumps(assignment, sort_keys=True)),
            },
        }
        if existing is not None:
            await self.client.request("PUT", f"{self._secrets(spec.namespace)}/{name}", json=secret)
        else:
            await self.client.request("POST", self._secrets(spec.namespace), json=secret)

    async def _claim(self, namespace: str, name: str) -> None:
        """Creates the run's SandboxClaim from what its Secret holds, then hands the Secret to
        the claim (owner reference): deleting the claim deletes the token."""
        secret = await self.client.request("GET", f"{self._secrets(namespace)}/{name}")
        assignment = _assignment(secret)
        shutdown = utcnow() + timedelta(minutes=int(assignment.get("timeout_minutes", 120)))
        claim = {
            "apiVersion": "extensions.agents.x-k8s.io/v1beta1",
            "kind": "SandboxClaim",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": {k: _label_value(v) for k, v in (assignment.get("labels") or {}).items()},
            },
            "spec": {
                "warmPoolRef": {"name": self.warm_pool},
                # The stage's budget, enforced by the controller even if nobody polls anymore.
                "lifecycle": {
                    "shutdownTime": shutdown.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "shutdownPolicy": "Delete",
                    "ttlSecondsAfterFinished": self.ttl_seconds,
                },
                # An annotation, not a label: claim labels must be in the controller's allowlisted
                # domains. It tells whoever looks at the pod which run it serves.
                "additionalPodMetadata": {
                    "annotations": {"choregos/run-id": str(assignment.get("run_id", ""))}
                },
            },
        }
        created = await self.client.request("POST", self._claims(namespace), json=claim)
        patch: dict[str, Any] = {"metadata": {"labels": {QUEUED: "false"}}}
        uid = ((created or {}).get("metadata") or {}).get("uid") if isinstance(created, dict) else None
        if uid:
            patch["metadata"]["ownerReferences"] = [
                {
                    "apiVersion": "extensions.agents.x-k8s.io/v1beta1",
                    "kind": "SandboxClaim",
                    "name": name,
                    "uid": uid,
                }
            ]
        await self.client.request(
            "PATCH",
            f"{self._secrets(namespace)}/{name}",
            json=patch,
            headers={"Content-Type": "application/merge-patch+json"},
        )

    # ───────────────────────────── queue ─────────────────────────────

    async def _queue(self, namespace: str) -> tuple[int, list[dict[str, Any]]]:
        """How many runs hold a sandbox, and which wait — oldest first."""
        claims = await self.client.request(
            "GET", self._claims(namespace), params={"labelSelector": "choregos/run-id"}
        )
        active = sum(1 for claim in (claims or {}).get("items", []) if not _finished(claim))
        secrets = await self.client.request(
            "GET", self._secrets(namespace), params={"labelSelector": f"{QUEUED}=true"}
        )
        waiting = sorted(
            (secrets or {}).get("items", []),
            key=lambda s: str((s.get("metadata") or {}).get("creationTimestamp", "")),
        )
        return active, waiting

    async def _admit(self, ref: ExecRef) -> bool:
        """Creates this run's claim if a slot is free AND it is next in arrival order."""
        namespace = ref.namespace or ""
        active, waiting = await self._queue(namespace)
        slots = self.max_active - active
        if slots <= 0 or ref.name not in {(s.get("metadata") or {}).get("name") for s in waiting[:slots]}:
            return False
        try:
            await self._claim(namespace, ref.name)
        except Exception:
            # A failed admission is not the end of the run: it stays queued, the next poll
            # retries, and the wait is visible ("waiting for a slot").
            return False
        return True

    # ───────────────────────────── status ─────────────────────────────

    async def status(self, ref: ExecRef) -> ExecStatus:
        namespace = ref.namespace or ""
        claim = await self.client.request("GET", f"{self._claims(namespace)}/{ref.name}")
        if claim is None:
            secret = await self.client.request("GET", f"{self._secrets(namespace)}/{ref.name}")
            if _labels(secret).get(QUEUED) != "true":
                return ExecStatus(state="unknown", message="SandboxClaim not found")
            if self.max_active and await self._admit(ref):
                return ExecStatus(state="pending")
            return ExecStatus(
                state="pending", message=f"waiting for a slot (cap {self.max_active} per namespace)"
            )
        sandbox_name = ((claim.get("status") or {}).get("sandbox") or {}).get("name")
        if not sandbox_name:
            return ExecStatus(state="pending")
        sandbox = await self.client.request(
            "GET", f"{SANDBOX_API}/namespaces/{namespace}/sandboxes/{sandbox_name}"
        )
        if sandbox is None:
            return ExecStatus(state="pending")
        assigned_at = _annotations(claim).get(ASSIGNED)
        ready = _condition(sandbox, "Ready")
        reason = str(ready.get("reason") or "")
        if reason == "PodSucceeded":
            return ExecStatus(
                state="succeeded", exit_code=0, started_at=_parse_time(assigned_at), ended_at=utcnow()
            )
        if reason == "PodFailed":
            code, cause = await self._diagnostic(namespace, sandbox)
            return ExecStatus(
                state="failed",
                exit_code=code,
                message=(cause or str(ready.get("message") or "pod failed"))[:700],
                started_at=_parse_time(assigned_at),
                ended_at=utcnow(),
            )
        if reason == "SandboxSuspended":
            return ExecStatus(state="pending", message="suspended: waiting for a person's answer")
        if reason == "SandboxExpired":
            return ExecStatus(state="failed", message="the sandbox expired (stage budget reached)")
        if ready.get("status") != "True":
            return ExecStatus(
                state="running" if assigned_at else "pending", started_at=_parse_time(assigned_at)
            )
        if not assigned_at:
            assigned_at = await self._assign(ref, claim, sandbox)
            if not assigned_at:
                return ExecStatus(state="pending")
        return ExecStatus(state="running", started_at=_parse_time(assigned_at))

    async def _assign(self, ref: ExecRef, claim: dict[str, Any], sandbox: dict[str, Any]) -> str | None:
        """Pushes the run to the pod of the sandbox bound to ITS claim; marks the claim.

        The only moment a token leaves its Secret, and it leaves SEALED (ADR 0053): to the public
        key the pod printed at boot, read here through the API server (`pods/log` — the API
        server reaches the kubelet over authenticated TLS, so the pod network cannot swap the
        key), and bound to the run id. Only the run id crosses the pod network in clear. A pod
        that printed no key gets nothing. The runner accepts one assignment: the same run again
        (a lost answer, a retried poll) is answered 200, another run 409 — and a 409 means this
        sandbox is not clean, so the run fails rather than share it.
        """
        namespace = ref.namespace or ""
        ips = (sandbox.get("status") or {}).get("podIPs") or []
        if not ips:
            return None
        pod = _annotations(sandbox).get(POD_NAME_ANNOTATION) or sandbox.get("metadata", {}).get("name")
        log = await self.client.request("GET", f"{CORE_API}/namespaces/{namespace}/pods/{pod}/log")
        public_key = key_from_log(str(log or ""))
        if public_key is None:
            return None  # no key printed (yet): nothing is sent
        secret = await self.client.request("GET", f"{self._secrets(namespace)}/{ref.name}")
        if secret is None:
            raise UpstreamError("kubernetes", f"the Secret of run {ref.run_id} is gone before its assignment")
        assignment = _assignment(secret)
        env = await self._shared_env(namespace)
        env.update({str(k): str(v) for k, v in (assignment.get("env") or {}).items()})
        run_id = str(assignment.get("run_id") or ref.run_id)
        payload = {
            "run_id": run_id,
            "sealed": seal(
                public_key,
                run_id,
                {
                    "api_url": assignment.get("api_url", ""),
                    "run_token": base64.b64decode((secret.get("data") or {}).get("token", "")).decode(),
                    "env": env,
                },
            ),
        }
        try:
            response = await self.http.post(
                f"http://{_host(ips[0])}:{self.assignment_port}/assign", json=payload
            )
        except httpx.HTTPError:
            return None  # not listening yet: the next poll retries
        if response.status_code == 409:
            raise UpstreamError(
                "agent-sandbox",
                f"sandbox {sandbox.get('metadata', {}).get('name')} already serves another run: refused",
                status_code=409,
            )
        if response.status_code >= 400:
            return None
        stamp = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        await self.client.request(
            "PATCH",
            f"{self._claims(namespace)}/{ref.name}",
            json={"metadata": {"annotations": {ASSIGNED: stamp}}},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        return stamp

    async def _shared_env(self, namespace: str) -> dict[str, str]:
        env: dict[str, str] = {}
        for name in self.env_from_secrets:
            secret = await self.client.request("GET", f"{self._secrets(namespace)}/{name}")
            for key, value in ((secret or {}).get("data") or {}).items():
                env[str(key)] = base64.b64decode(value).decode()
        return env

    async def _pod_name(self, namespace: str, name: str) -> str | None:
        claim = await self.client.request("GET", f"{self._claims(namespace)}/{name}")
        sandbox_name = (((claim or {}).get("status") or {}).get("sandbox") or {}).get("name")
        if not sandbox_name:
            return None
        sandbox = await self.client.request(
            "GET", f"{SANDBOX_API}/namespaces/{namespace}/sandboxes/{sandbox_name}"
        )
        return _annotations(sandbox).get(POD_NAME_ANNOTATION) or str(sandbox_name)

    async def _diagnostic(self, namespace: str, sandbox: dict[str, Any]) -> tuple[int | None, str]:
        """The runner container's exit code and reason, read on the sandbox's pod. Never logs."""
        name = _annotations(sandbox).get(POD_NAME_ANNOTATION) or sandbox.get("metadata", {}).get("name")
        pod = await self.client.request("GET", f"{CORE_API}/namespaces/{namespace}/pods/{name}")
        states = ((pod or {}).get("status") or {}).get("containerStatuses") or []
        runner = next((c for c in states if c.get("name") == "runner"), states[0] if states else None)
        if runner is None:
            return None, ""
        terminated = (runner.get("state") or {}).get("terminated") or (runner.get("lastState") or {}).get(
            "terminated"
        )
        if terminated:
            code = terminated.get("exitCode")
            meaning = explication(code)
            reason = str(terminated.get("reason") or "terminated")
            return code, f"runner exited with {code} ({reason}{': ' + meaning if meaning else ''})"
        waiting = (runner.get("state") or {}).get("waiting")
        if waiting:
            return None, f"runner never started: {waiting.get('reason')}"
        return None, ""

    # ──────────────────────── suspend and resume (S35-03) ────────────────────────

    async def suspend(self, ref: ExecRef, until: datetime) -> bool:
        """Suspends the run's sandbox while a person answers its agent's question.

        The pod goes; the sandbox and its workspace volume stay. The claim's `shutdownTime`
        becomes `until` (the policy's `sandbox.suspend_max_hours`), and its
        `ttlSecondsAfterFinished` is dropped — the runner exited after asking, and that clock
        would otherwise delete the sandbox an hour later. So the bound is held by the
        controller itself: past it, the claim, the sandbox and the volume are deleted even if
        no workflow is there to say so. False when there is nothing left to suspend.
        """
        namespace = ref.namespace or ""
        claim = await self.client.request("GET", f"{self._claims(namespace)}/{ref.name}")
        sandbox_name = (((claim or {}).get("status") or {}).get("sandbox") or {}).get("name")
        if not sandbox_name:
            return False
        await self._patch_claim(
            namespace,
            ref.name,
            {"spec": {"lifecycle": {"shutdownTime": _rfc3339(until), "ttlSecondsAfterFinished": None}}},
        )
        await self.client.request(
            "PATCH",
            f"{SANDBOX_API}/namespaces/{namespace}/sandboxes/{sandbox_name}",
            json={"spec": {"operatingMode": "Suspended"}},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        return True

    async def resume(self, ref: ExecRef, run_token: str, until: datetime) -> bool:
        """Brings the suspended sandbox back for the SAME run, with its workspace.

        A new pod comes up on the same volume and waits for an assignment, like a warm one: the
        next `status` pushes it the run again, with `run_token` — a fresh one, the first having
        expired during the wait. `until` is the new stage deadline. False when the sandbox is
        gone (the bound passed, someone deleted it): the caller then replays the stage.
        """
        namespace = ref.namespace or ""
        claim = await self.client.request("GET", f"{self._claims(namespace)}/{ref.name}")
        sandbox_name = (((claim or {}).get("status") or {}).get("sandbox") or {}).get("name")
        if not sandbox_name:
            return False
        sandbox = await self.client.request(
            "GET", f"{SANDBOX_API}/namespaces/{namespace}/sandboxes/{sandbox_name}"
        )
        if sandbox is None:
            return False
        await self.renew(ref, run_token)
        await self._patch_claim(
            namespace,
            ref.name,
            {
                "metadata": {"annotations": {ASSIGNED: None}},
                "spec": {
                    "lifecycle": {
                        "shutdownTime": _rfc3339(until),
                        "ttlSecondsAfterFinished": self.ttl_seconds,
                    }
                },
            },
        )
        await self.client.request(
            "PATCH",
            f"{SANDBOX_API}/namespaces/{namespace}/sandboxes/{sandbox_name}",
            json={"spec": {"operatingMode": "Running"}},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        return True

    async def _patch_claim(self, namespace: str, name: str, patch: dict[str, Any]) -> None:
        await self.client.request(
            "PATCH",
            f"{self._claims(namespace)}/{name}",
            json=patch,
            headers={"Content-Type": "application/merge-patch+json"},
        )

    # ───────────────────────────── the rest ─────────────────────────────

    async def renew(self, ref: ExecRef, run_token: str) -> None:
        """Replaces the token of a run not yet assigned. Its Secret is the only copy until the
        push; once pushed, the pod has its token and this changes nothing it reads."""
        namespace = ref.namespace or ""
        secret = await self.client.request("GET", f"{self._secrets(namespace)}/{ref.name}")
        if secret is None:
            return
        secret.setdefault("data", {})["token"] = _b64(run_token)
        await self.client.request("PUT", f"{self._secrets(namespace)}/{ref.name}", json=secret)

    async def logs(self, ref: ExecRef) -> AsyncIterator[str]:
        """The runner pod's log, as `k8s_job` and `tekton` give theirs (S36 archives it)."""
        namespace = ref.namespace or ""
        pod = await self._pod_name(namespace, ref.name)
        if not pod:
            return
        text = await self.client.request("GET", f"{CORE_API}/namespaces/{namespace}/pods/{pod}/log")
        for line in str(text or "").splitlines():
            yield line

    async def cancel(self, ref: ExecRef) -> None:
        """Deletes the claim — the controller deletes its sandbox, pod and volume — and the
        run's Secret, which a queued run has without a claim to own it."""
        namespace = ref.namespace or ""
        await self.client.request(
            "DELETE", f"{self._claims(namespace)}/{ref.name}", json={"propagationPolicy": "Background"}
        )
        await self.client.request("DELETE", f"{self._secrets(namespace)}/{ref.name}")


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _labels(obj: Any) -> dict[str, str]:
    return dict(((obj or {}).get("metadata") or {}).get("labels") or {}) if isinstance(obj, dict) else {}


def _annotations(obj: Any) -> dict[str, str]:
    return dict(((obj or {}).get("metadata") or {}).get("annotations") or {}) if isinstance(obj, dict) else {}


def _assignment(secret: Any) -> dict[str, Any]:
    raw = ((secret or {}).get("data") or {}).get("assignment", "")
    data: dict[str, Any] = json.loads(base64.b64decode(raw).decode()) if raw else {}
    return data


def _condition(obj: Any, kind: str) -> dict[str, Any]:
    for condition in ((obj or {}).get("status") or {}).get("conditions") or []:
        if condition.get("type") == kind:
            return dict(condition)
    return {}


def _finished(claim: dict[str, Any]) -> bool:
    if _condition(claim, "Finished").get("status") == "True":
        return True
    return str(_condition(claim, "Ready").get("reason") or "") in {
        "PodSucceeded",
        "PodFailed",
        "SandboxExpired",
    }


def _label_value(value: Any) -> str:
    text = "".join(c if c.isalnum() or c in "-_." else "-" for c in str(value))[:63]
    return text.strip("-_.")


def _rfc3339(instant: datetime) -> str:
    """The instant in UTC, as Kubernetes writes it. A naive one is taken as UTC."""
    if instant.tzinfo is not None:
        instant = instant.astimezone(UTC)
    return instant.strftime("%Y-%m-%dT%H:%M:%SZ")


def _host(ip: str) -> str:
    return f"[{ip}]" if ":" in ip else ip

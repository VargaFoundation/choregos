# SPDX-License-Identifier: Apache-2.0
"""A scriptable agent-sandbox cluster, in memory (ADR 0005): the Kubernetes API the
`agent_sandbox` executor talks to, and the controller behind it, reduced to what it relies on.

`FakeSandboxCluster` answers the executor's REST calls (Secrets, Pods, SandboxClaims, Sandboxes)
the way the API server does — 404 as `None`, merge patches, label selectors — and plays the
operator's part when told to:

- `warm(n)` makes `n` sandboxes from the template, Ready, never used;
- `reconcile()` binds each unbound claim to a warm sandbox, or cold-starts one, deletes the
  sandbox of a deleted claim, and follows `operatingMode` (Suspended deletes the pod and keeps
  the volume; Running brings a NEW pod back);
- `finish(run_id, exit_code)` ends the pod serving a run.

Its `transport` stands for the pod network: the executor's push to `http://<pod ip>:<port>/assign`
lands in the pod's `wire` as sent, and in its `assignments` once opened with the pod's key, so a
test can say what crossed the network and which pod could read which token. Each pod prints its
public key in its log at boot, as the real runner does. A pod opens one run; the same run again
is 200, another is 409, what does not open is 400, as the real runner answers.
"""

from __future__ import annotations

import copy
import itertools
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..errors import UpstreamError
from ..sealing import SealError, key_line, new_key, open_sealed

CLAIMS = "/apis/extensions.agents.x-k8s.io/v1beta1/namespaces/"
SANDBOXES = "/apis/agents.x-k8s.io/v1beta1/namespaces/"
CORE = "/api/v1/namespaces/"


@dataclass
class FakePod:
    name: str
    ip: str
    sandbox: str
    phase: str = "Running"
    ready: bool = True
    exit_code: int | None = None
    log: list[str] = field(default_factory=list)
    #: Every assignment this pod OPENED, in order: `{run_id, api_url, run_token, env}`.
    assignments: list[dict[str, Any]] = field(default_factory=list)
    #: The raw bodies that reached this pod over the network: what an eavesdropper reads.
    wire: list[bytes] = field(default_factory=list)
    #: The pod's private key, as the real runner holds it: in memory, dropped once used.
    private: Any = None


@dataclass
class FakeSandbox:
    name: str
    namespace: str
    cold: bool
    claim: str | None = None
    mode: str = "Running"
    pod: FakePod | None = None
    #: The workspace volume: kept across a suspension, gone with the sandbox.
    volume: dict[str, str] = field(default_factory=dict)
    #: Every pod this sandbox ever had (a resumed sandbox gets a new one).
    pods: list[FakePod] = field(default_factory=list)


class FakeSandboxCluster:
    def __init__(self, namespace: str = "choregos") -> None:
        self.namespace = namespace
        #: False plays a pod whose runner never printed its key (another image, a crash).
        self.publish_keys = True
        self.secrets: dict[str, dict[str, Any]] = {}
        self.claims: dict[str, dict[str, Any]] = {}
        self.sandboxes: dict[str, FakeSandbox] = {}
        #: Sandboxes deleted, by name: a sandbox is never handed out twice.
        self.deleted: list[str] = []
        self.calls: list[tuple[str, str]] = []
        #: Status codes forced on the next requests (`(method, path suffix)` → code).
        self.refuse: dict[tuple[str, str], int] = {}
        self._ids = itertools.count(1)
        self._clock = itertools.count(1)

    # ───────────────────────── the operator's part ─────────────────────────

    def warm(self, n: int) -> list[str]:
        made = []
        for _ in range(n):
            sandbox = self._new_sandbox(cold=False)
            made.append(sandbox.name)
        return made

    def warm_pool(self) -> list[str]:
        return [s.name for s in self.sandboxes.values() if s.claim is None and not s.cold]

    def reconcile(self) -> None:
        for name, claim in list(self.claims.items()):
            if ((claim.get("status") or {}).get("sandbox") or {}).get("name"):
                continue
            sandbox = next((s for s in self.sandboxes.values() if s.claim is None and not s.cold), None)
            if sandbox is None:
                sandbox = self._new_sandbox(cold=True)
            sandbox.claim = name
            claim.setdefault("status", {})["sandbox"] = {"name": sandbox.name}
        for sandbox in list(self.sandboxes.values()):
            if sandbox.claim is not None and sandbox.claim not in self.claims:
                self._delete_sandbox(sandbox.name)
                continue
            if sandbox.mode == "Suspended" and sandbox.pod is not None:
                sandbox.pod = None
            elif sandbox.mode == "Running" and sandbox.pod is None:
                sandbox.pod = self._new_pod(sandbox)

    def finish(self, run_id: str, exit_code: int = 0) -> None:
        pod = self.pod_of(run_id)
        assert pod is not None, f"no pod serves {run_id}"
        pod.phase = "Succeeded" if exit_code == 0 else "Failed"
        pod.ready = False
        pod.exit_code = exit_code

    def expire(self, run_id: str) -> None:
        """The claim's `shutdownTime` passed: the controller deletes claim and sandbox."""
        name = f"run-{run_id}"
        self.claims.pop(name, None)
        self._gc_secrets(name)
        self.reconcile()

    def sandbox_of(self, run_id: str) -> FakeSandbox | None:
        return next((s for s in self.sandboxes.values() if s.claim == f"run-{run_id}"), None)

    def pod_of(self, run_id: str) -> FakePod | None:
        sandbox = self.sandbox_of(run_id)
        return sandbox.pod if sandbox else None

    def all_pods(self) -> Iterator[FakePod]:
        for sandbox in self.sandboxes.values():
            yield from sandbox.pods

    def _new_sandbox(self, *, cold: bool) -> FakeSandbox:
        name = f"choregos-runner-{next(self._ids):04d}"
        sandbox = FakeSandbox(name=name, namespace=self.namespace, cold=cold)
        sandbox.pod = self._new_pod(sandbox)
        self.sandboxes[name] = sandbox
        return sandbox

    def _new_pod(self, sandbox: FakeSandbox) -> FakePod:
        pod = FakePod(name=sandbox.name, ip=f"10.0.0.{next(self._ids)}", sandbox=sandbox.name)
        if self.publish_keys:  # what `choregos-runner await-assignment` prints at boot
            pod.private, public = new_key()
            pod.log.append(key_line(public))
        sandbox.pods.append(pod)
        return pod

    def _delete_sandbox(self, name: str) -> None:
        self.sandboxes.pop(name, None)
        self.deleted.append(name)

    def _gc_secrets(self, claim: str) -> None:
        for key, secret in list(self.secrets.items()):
            owners = (secret.get("metadata") or {}).get("ownerReferences") or []
            if any(o.get("kind") == "SandboxClaim" and o.get("name") == claim for o in owners):
                self.secrets.pop(key)

    # ───────────────────────── the API server's part ─────────────────────────

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path))
        for (m, suffix), code in list(self.refuse.items()):
            if m == method and path.endswith(suffix):
                del self.refuse[(m, suffix)]
                raise UpstreamError("kubernetes", f"{method} {path} → {code}", status_code=code)
        body = kwargs.get("json")
        selector = (kwargs.get("params") or {}).get("labelSelector")
        if path.startswith(CLAIMS) and "/sandboxclaims" in path:
            return self._claims(method, path, body, selector)
        if path.startswith(SANDBOXES) and "/sandboxes" in path:
            return self._sandboxes(method, path, body)
        if path.startswith(CORE) and "/secrets" in path:
            return self._secrets(method, path, body, selector)
        if path.startswith(CORE) and "/pods/" in path:
            return self._pods(method, path)
        raise AssertionError(f"unexpected call {method} {path}")

    def _meta(self, obj: dict[str, Any]) -> dict[str, Any]:
        meta = obj.setdefault("metadata", {})
        meta.setdefault("uid", f"uid-{next(self._ids)}")
        meta.setdefault("creationTimestamp", f"2026-10-12T00:00:{next(self._clock):02d}Z")
        return obj

    def _claims(self, method: str, path: str, body: Any, selector: str | None) -> Any:
        tail = path.split("/sandboxclaims", 1)[1].strip("/")
        if method == "GET" and not tail:
            return {"items": [self._claim_json(n) for n, c in self.claims.items() if _matches(c, selector)]}
        if method == "POST":
            claim = self._meta(copy.deepcopy(body))
            name = claim["metadata"]["name"]
            if name in self.claims:
                raise UpstreamError("kubernetes", f"POST {path} → 409", status_code=409)
            self.claims[name] = claim
            return copy.deepcopy(claim)
        if tail not in self.claims:
            return None
        if method == "GET":
            return self._claim_json(tail)
        if method == "PATCH":
            _merge(self.claims[tail], body)
            return copy.deepcopy(self.claims[tail])
        if method == "DELETE":
            del self.claims[tail]
            self._gc_secrets(tail)
            self.reconcile()
            return {}
        raise AssertionError(f"unexpected {method} {path}")

    def _claim_json(self, name: str) -> dict[str, Any]:
        """The claim as the controller reports it: `Finished` mirrored from its sandbox's pod."""
        claim = copy.deepcopy(self.claims[name])
        sandbox = next((s for s in self.sandboxes.values() if s.claim == name), None)
        if sandbox is not None and sandbox.pod is not None and sandbox.pod.phase != "Running":
            claim.setdefault("status", {})["conditions"] = [{"type": "Finished", "status": "True"}]
        return claim

    def _sandboxes(self, method: str, path: str, body: Any) -> Any:
        name = path.split("/sandboxes", 1)[1].strip("/")
        sandbox = self.sandboxes.get(name)
        if sandbox is None:
            return None
        if method == "PATCH":
            mode = ((body or {}).get("spec") or {}).get("operatingMode")
            if mode:
                sandbox.mode = mode
            self.reconcile()
            return self._sandbox_json(sandbox)
        if method == "GET":
            return self._sandbox_json(sandbox)
        raise AssertionError(f"unexpected {method} {path}")

    def _sandbox_json(self, sandbox: FakeSandbox) -> dict[str, Any]:
        pod = sandbox.pod
        conditions: list[dict[str, Any]] = []
        if sandbox.mode == "Suspended":
            conditions.append({"type": "Ready", "status": "False", "reason": "Suspended"})
            conditions.append({"type": "Suspended", "status": "True" if pod is None else "False"})
        elif pod is None:
            conditions.append({"type": "Ready", "status": "False", "reason": "DependenciesNotReady"})
        elif pod.phase == "Succeeded":
            conditions.append({"type": "Ready", "status": "False", "reason": "PodSucceeded"})
        elif pod.phase == "Failed":
            conditions.append({"type": "Ready", "status": "False", "reason": "PodFailed"})
        else:
            conditions.append(
                {"type": "Ready", "status": "True" if pod.ready else "False", "reason": "DependenciesReady"}
            )
        return {
            "apiVersion": "agents.x-k8s.io/v1beta1",
            "kind": "Sandbox",
            "metadata": {"name": sandbox.name, "namespace": sandbox.namespace},
            "spec": {"operatingMode": sandbox.mode},
            "status": {"conditions": conditions, "podIPs": [pod.ip] if pod else []},
        }

    def _secrets(self, method: str, path: str, body: Any, selector: str | None) -> Any:
        name = path.split("/secrets", 1)[1].strip("/")
        if method == "GET" and not name:
            return {"items": [copy.deepcopy(s) for s in self.secrets.values() if _matches(s, selector)]}
        if method == "POST":
            secret = self._meta(copy.deepcopy(body))
            self.secrets[secret["metadata"]["name"]] = secret
            return copy.deepcopy(secret)
        if name not in self.secrets:
            return None
        if method == "GET":
            return copy.deepcopy(self.secrets[name])
        if method == "PUT":
            kept = self.secrets[name]["metadata"]
            replaced = copy.deepcopy(body)
            for key in ("uid", "creationTimestamp", "ownerReferences"):
                if key in kept:
                    replaced["metadata"].setdefault(key, kept[key])
            self.secrets[name] = replaced
            return copy.deepcopy(replaced)
        if method == "PATCH":
            _merge(self.secrets[name], body)
            return copy.deepcopy(self.secrets[name])
        if method == "DELETE":
            del self.secrets[name]
            return {}
        raise AssertionError(f"unexpected {method} {path}")

    def _pods(self, method: str, path: str) -> Any:
        rest = path.split("/pods/", 1)[1]
        name, _, sub = rest.partition("/")
        pod = next((s.pod for s in self.sandboxes.values() if s.pod and s.pod.name == name), None)
        if pod is None:
            return None
        if sub == "log":
            return "\n".join(pod.log)
        state: dict[str, Any] = (
            {"terminated": {"exitCode": pod.exit_code, "reason": "Error" if pod.exit_code else "Completed"}}
            if pod.exit_code is not None
            else {"running": {}}
        )
        return {
            "metadata": {"name": pod.name},
            "status": {"containerStatuses": [{"name": "runner", "state": state}]},
        }

    # ───────────────────────── the pod network ─────────────────────────

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._push)

    def _push(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        pod = next((s.pod for s in self.sandboxes.values() if s.pod and s.pod.ip == host), None)
        if pod is None or not pod.ready or request.url.path != "/assign":
            raise httpx.ConnectError("connection refused", request=request)
        pod.wire.append(bytes(request.content))
        payload = json.loads(request.content)
        if pod.assignments:
            if pod.assignments[0]["run_id"] != payload["run_id"]:
                return httpx.Response(409, json={"error": "already assigned"})
            return httpx.Response(200, json={"run_id": payload["run_id"]})
        if pod.private is None:
            return httpx.Response(400, json={"error": "no key"})
        try:
            opened = open_sealed(pod.private, payload["run_id"], payload["sealed"])
        except SealError:
            return httpx.Response(400, json={"error": "not sealed to this pod"})
        pod.private = None
        pod.assignments.append({"run_id": payload["run_id"], **opened})
        return httpx.Response(200, json={"run_id": payload["run_id"]})


def _matches(obj: dict[str, Any], selector: str | None) -> bool:
    if not selector:
        return True
    labels = (obj.get("metadata") or {}).get("labels") or {}
    for term in selector.split(","):
        key, _, value = term.partition("=")
        if key not in labels or (value and labels[key] != value):
            return False
    return True


def _merge(target: dict[str, Any], patch: Any) -> None:
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        elif value is None:
            target.pop(key, None)
        else:
            target[key] = copy.deepcopy(value)

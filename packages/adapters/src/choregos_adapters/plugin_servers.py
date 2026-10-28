# SPDX-License-Identifier: Apache-2.0
"""A plugin's MCP server, as the platform runs it in Kubernetes (ADR 0096, plan S43 Decision 2).

`server_manifests` turns one server into the objects that run it in the plugins namespace, and
`PluginServerRuntime` applies them through the same `KubernetesClient` the executors use (fake:
`FakeKubernetesApi`). Applying is idempotent: an absent object is created, a present one replaced, so
replaying an install, an upgrade or a configuration change converges on the same objects.

What runs:

- a **Secret** with the server's configuration as environment variables and the platform's bearer
  token (`CHOREGOS_SERVER_TOKEN`), the server refusing any call without it;
- a **Deployment** of one replica (none while the configuration is incomplete), the image pinned by
  digest, non-root, read-only root filesystem, no ServiceAccount token, capabilities dropped,
  resource limits, the RuntimeClass the deployment names (gVisor). The digest of the Secret's data
  is an annotation of the pod template: changing the configuration rolls the pods;
- a **Service** in front of it, which only the API calls;
- a **NetworkPolicy**: ingress from the API pods only, on the server's port; egress to DNS and, by
  default, to public addresses on the ports of the hosts its configuration names. Kubernetes cannot
  name a host: with `egress="cilium"` a `CiliumNetworkPolicy` narrows egress to those exact names
  (`toFQDNs`) and the NetworkPolicy keeps DNS only.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from .executor.tekton import CORE_API, KubernetesClient
from .mcp.client import ClientMcp

APPS_API = "/apis/apps/v1"
NETWORKING_API = "/apis/networking.k8s.io/v1"
CILIUM_API = "/apis/cilium.io/v2"
#: The variable the server reads its bearer token from; it refuses `/mcp` without it (docs/plugins.md).
TOKEN_ENV = "CHOREGOS_SERVER_TOKEN"  # noqa: S105 - a variable name, not a secret
CONFIG_DIGEST = "choregos.dev/config-digest"
APP_NAME = "choregos-plugin-server"
#: Addresses a server never reaches by the default NetworkPolicy: the cluster's, the node's, the
#: cloud's metadata endpoint. Its egress is the internet, on the ports its configuration names.
PRIVATE_RANGES = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10")

EgressMode = Literal["networkpolicy", "cilium"]


@dataclass(frozen=True)
class ServerDeployment:
    """One server of one organisation, everything its objects need."""

    name: str
    namespace: str
    image: str
    port: int
    health_path: str
    #: the Secret's data: the token and the configuration, as environment variables
    env: dict[str, str]
    labels: dict[str, str]
    replicas: int = 1
    runtime_class: str | None = None
    cpu: str = "500m"
    memory: str = "512Mi"
    #: the API's pods, the only ones admitted (`kubernetes.io/metadata.name` of their namespace)
    api_namespace: str = ""
    api_labels: dict[str, str] = field(default_factory=lambda: {"app.kubernetes.io/name": "choregos-api"})
    #: (host, port) the server may reach, derived from its configuration
    egress: tuple[tuple[str, int], ...] = ()
    egress_mode: EgressMode = "networkpolicy"

    @property
    def config_digest(self) -> str:
        canonical = json.dumps(self.env, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()


def _meta(d: ServerDeployment) -> dict[str, Any]:
    return {"name": d.name, "namespace": d.namespace, "labels": dict(d.labels)}


def _secret(d: ServerDeployment) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _meta(d),
        "type": "Opaque",
        "data": {k: base64.b64encode(v.encode()).decode() for k, v in sorted(d.env.items())},
    }


def _deployment(d: ServerDeployment) -> dict[str, Any]:
    selector = {"app.kubernetes.io/instance": d.name}
    pod: dict[str, Any] = {
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 65532,
            "runAsGroup": 65532,
            "fsGroup": 65532,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "server",
                "image": d.image,
                "imagePullPolicy": "IfNotPresent",
                "ports": [{"name": "mcp", "containerPort": d.port, "protocol": "TCP"}],
                "envFrom": [{"secretRef": {"name": d.name}}],
                "resources": {
                    "requests": {"cpu": "50m", "memory": "64Mi"},
                    "limits": {"cpu": d.cpu, "memory": d.memory},
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "runAsNonRoot": True,
                    "capabilities": {"drop": ["ALL"]},
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "readinessProbe": {"httpGet": {"path": d.health_path, "port": "mcp"}, "periodSeconds": 10},
                "livenessProbe": {
                    "httpGet": {"path": d.health_path, "port": "mcp"},
                    "periodSeconds": 30,
                    "failureThreshold": 4,
                },
                "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],  # noqa: S108 - an emptyDir
            }
        ],
        "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
    }
    if d.runtime_class:
        pod["runtimeClassName"] = d.runtime_class
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _meta(d),
        "spec": {
            "replicas": d.replicas,
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {
                    "labels": {**d.labels, **selector},
                    "annotations": {CONFIG_DIGEST: d.config_digest},
                },
                "spec": pod,
            },
        },
    }


def _service(d: ServerDeployment) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(d),
        "spec": {
            "selector": {"app.kubernetes.io/instance": d.name},
            "ports": [{"name": "mcp", "port": d.port, "targetPort": "mcp", "protocol": "TCP"}],
        },
    }


_DNS = {
    "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}],
    "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
}


def _ports(egress: Iterable[tuple[str, int]]) -> list[dict[str, Any]]:
    return [{"protocol": "TCP", "port": p} for p in sorted({port for _, port in egress})]


def _network_policy(d: ServerDeployment) -> dict[str, Any]:
    source: dict[str, Any] = {"podSelector": {"matchLabels": dict(d.api_labels)}}
    source["namespaceSelector"] = (
        {"matchLabels": {"kubernetes.io/metadata.name": d.api_namespace}} if d.api_namespace else {}
    )
    egress: list[dict[str, Any]] = []
    if d.egress:
        egress.append(_DNS)
        if d.egress_mode == "networkpolicy":
            egress.append(
                {
                    "to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": list(PRIVATE_RANGES)}}],
                    "ports": _ports(d.egress),
                }
            )
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": _meta(d),
        "spec": {
            "podSelector": {"matchLabels": {"app.kubernetes.io/instance": d.name}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [{"from": [source], "ports": [{"protocol": "TCP", "port": d.port}]}],
            "egress": egress,
        },
    }


def _cilium_policy(d: ServerDeployment) -> dict[str, Any]:
    dns = {
        "toEndpoints": [
            {"matchLabels": {"k8s:io.kubernetes.pod.namespace": "kube-system", "k8s-app": "kube-dns"}}
        ],
        "toPorts": [
            {
                "ports": [{"port": "53", "protocol": "ANY"}],
                "rules": {"dns": [{"matchName": host} for host in sorted({h for h, _ in d.egress})]},
            }
        ],
    }
    fqdns = {
        "toFQDNs": [{"matchName": host} for host in sorted({h for h, _ in d.egress})],
        "toPorts": [{"ports": [{"port": str(p["port"]), "protocol": "TCP"} for p in _ports(d.egress)]}],
    }
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": _meta(d),
        "spec": {
            "endpointSelector": {"matchLabels": {"app.kubernetes.io/instance": d.name}},
            "egress": [dns, fqdns] if d.egress else [],
        },
    }


def server_manifests(d: ServerDeployment) -> list[dict[str, Any]]:
    """The objects that run `d`, in the order they are applied (the Secret before its pods)."""
    objects = [_secret(d), _service(d), _network_policy(d), _deployment(d)]
    if d.egress_mode == "cilium":
        objects.insert(3, _cilium_policy(d))
    return objects


_PATHS = {
    "Secret": (CORE_API, "secrets"),
    "Service": (CORE_API, "services"),
    "Deployment": (APPS_API, "deployments"),
    "NetworkPolicy": (NETWORKING_API, "networkpolicies"),
    "CiliumNetworkPolicy": (CILIUM_API, "ciliumnetworkpolicies"),
}


def _collection(kind: str, namespace: str) -> str:
    api, resource = _PATHS[kind]
    return f"{api}/namespaces/{namespace}/{resource}"


def _covers(existing: Any, desired: Any) -> bool:
    """`existing` already holds everything `desired` says (the API server adds defaults and status)."""
    if isinstance(desired, dict):
        return isinstance(existing, dict) and all(_covers(existing.get(k), v) for k, v in desired.items())
    if isinstance(desired, list):
        return (
            isinstance(existing, list)
            and len(existing) == len(desired)
            and all(_covers(e, d) for e, d in zip(existing, desired, strict=True))
        )
    return bool(existing == desired)


class PluginServerRuntime:
    """Applies and deletes the objects of plugin servers in one namespace."""

    def __init__(self, client: KubernetesClient | Any, namespace: str, *, cilium: bool = False) -> None:
        self.client = client
        self.namespace = namespace
        self.cilium = cilium

    async def apply(self, d: ServerDeployment) -> None:
        """Creates what is absent, replaces what differs, leaves what already matches: replayed, nothing
        is written."""
        for obj in server_manifests(d):
            path = f"{_collection(obj['kind'], d.namespace)}/{d.name}"
            existing = await self.client.request("GET", path)
            if existing is None:
                await self.client.request("POST", _collection(obj["kind"], d.namespace), json=obj)
            elif _covers(existing, obj):
                continue  # replayed: nothing to write
            elif obj["kind"] == "Service":
                # A Service's `clusterIP` is immutable and a PUT without it is refused: merge instead.
                await self.client.request(
                    "PATCH", path, json=obj, headers={"Content-Type": "application/merge-patch+json"}
                )
            else:
                version = (existing.get("metadata") or {}).get("resourceVersion")
                if version:
                    obj["metadata"]["resourceVersion"] = version
                await self.client.request("PUT", path, json=obj)

    async def delete(self, name: str) -> None:
        """Deletes the server's objects; an absent one is already deleted."""
        kinds = ["Deployment", "Service", "NetworkPolicy", "Secret"]
        if self.cilium:
            kinds.insert(3, "CiliumNetworkPolicy")
        for kind in kinds:
            await self.client.request("DELETE", f"{_collection(kind, self.namespace)}/{name}")


class PluginServerClient(ClientMcp):
    """The MCP client of a plugin server: the platform's call to a Service of its own cluster.

    It posts to one URL only, the Service's, which the platform wrote (never an administrator, never
    a plugin), and follows no redirect: it is not an egress of the organisation, so it does not pass
    the organisation's egress allowlist the way a call to the internet does (ADR 0066). The bearer
    token is the server's own (`CHOREGOS_SERVER_TOKEN`), presented on every call."""

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport or httpx.AsyncHTTPTransport(),
            follow_redirects=False,
        )


__all__ = [
    "CONFIG_DIGEST",
    "PRIVATE_RANGES",
    "TOKEN_ENV",
    "PluginServerClient",
    "PluginServerRuntime",
    "ServerDeployment",
    "server_manifests",
]

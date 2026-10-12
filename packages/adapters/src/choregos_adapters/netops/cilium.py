# SPDX-License-Identifier: Apache-2.0
"""Cilium: its network policies through the Kubernetes API, its flows through Hubble (ADR 0052).

Read-only BY CONSTRUCTION, twice:
- the Kubernetes client below has `get` and nothing else — there is no code path that sends a
  verb other than GET, so no configuration mistake turns it into a `kubectl apply`;
- the token it presents belongs to a dedicated service account whose ClusterRole grants `get`,
  `list` on `ciliumnetworkpolicies` and `ciliumclusterwidenetworkpolicies` only (ADR 0052 gives the
  manifest). The connector never falls back to the pod's own service account: a platform pod's
  token is wider than what this connector reads.

A policy change is never applied here: it is proposed as a pull request to the GitOps repository
(S33-09), and Argo CD applies what the release train merged (ADR 0006, ADR 0009).
"""

from __future__ import annotations

from typing import Any

import httpx

from ..errors import AdapterError, UpstreamError
from ..registry import OperationSpec
from .hubble import FlowFilter, FlowSource, HubbleCli
from .tls import tls_verification

_TEXT: dict[str, Any] = {"type": "string"}
CILIUM_API = "/apis/cilium.io/v2"


def _schema(**properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": [], "additionalProperties": False}


CILIUM_READ_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "list_network_policies",
        "read",
        "lists the CiliumNetworkPolicies of a namespace (all namespaces when none is given)",
        _schema(namespace=_TEXT),
    ),
    OperationSpec(
        "list_clusterwide_policies",
        "read",
        "lists the CiliumClusterwideNetworkPolicies",
        _schema(),
    ),
    OperationSpec(
        "get_flows",
        "read",
        "reads recent Hubble flows, normalised: who talks to whom, the verdict and, for a drop, the reason "
        "and the policies that denied it",
        _schema(
            namespace=_TEXT,
            pod={"type": "string", "description": "a pod name or its prefix"},
            verdict={"type": "string", "enum": ["FORWARDED", "DROPPED", "ERROR", "AUDIT"]},
            since={"type": "string", "description": "a duration such as 10m, or an RFC 3339 time"},
            last={"type": "integer", "minimum": 1, "maximum": 2000},
        ),
    ),
)


class ReadOnlyKubernetes:
    """The Kubernetes API, reads only. `transport` is the seam the fake plugs into."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        verify: Any = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token:
            raise AdapterError(
                "cilium: no service account token (`token`, written as a reference) — the connector "
                "never borrows the pod's own"
            )
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(timeout=20.0, verify=verify, transport=transport)

    async def get(self, path: str) -> dict[str, Any]:
        response = await self._client.get(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise UpstreamError(
                "kubernetes",
                f"GET {path} → {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )
        body: dict[str, Any] = response.json()
        return body


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    """A policy as an agent reads it: its name, namespace and spec — not its managed fields."""
    metadata = item.get("metadata") or {}
    return {
        "kind": item.get("kind"),
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels") or {},
        "spec": item.get("spec"),
        "specs": item.get("specs"),
    }


class CiliumNetwork:
    service = "cilium"
    operations: tuple[OperationSpec, ...] = CILIUM_READ_OPERATIONS

    def __init__(self, kubernetes: ReadOnlyKubernetes, flows: FlowSource) -> None:
        self.kubernetes = kubernetes
        self.flow_source = flows

    @classmethod
    def from_config(cls, config: dict[str, Any], *, transport: httpx.AsyncBaseTransport | None = None) -> Any:
        kubernetes = ReadOnlyKubernetes(
            str(config["kubernetes_url"]),
            str(config.get("token") or ""),
            verify=tls_verification("cilium", config),
            transport=transport,
        )
        hubble = HubbleCli(
            server=str(config.get("hubble_server") or "hubble-relay.kube-system.svc:443"),
            binary=str(config.get("hubble_binary") or "hubble"),
            tls=bool(config.get("hubble_tls", True)),
            ca_cert=str(config.get("hubble_ca_cert") or ""),
            server_name=str(config.get("hubble_tls_server_name") or ""),
            client_cert=str(config.get("hubble_client_cert") or ""),
            client_key=str(config.get("hubble_client_key") or ""),
        )
        return cls(kubernetes, hubble)

    async def list_network_policies(self, namespace: str | None = None) -> dict[str, Any]:
        path = (
            f"{CILIUM_API}/namespaces/{namespace}/ciliumnetworkpolicies"
            if namespace
            else f"{CILIUM_API}/ciliumnetworkpolicies"
        )
        items = (await self.kubernetes.get(path)).get("items") or []
        policies = [_summary({"kind": "CiliumNetworkPolicy", **item}) for item in items]
        return {"policies": policies, "count": len(policies)}

    async def list_clusterwide_policies(self) -> dict[str, Any]:
        items = (await self.kubernetes.get(f"{CILIUM_API}/ciliumclusterwidenetworkpolicies")).get(
            "items"
        ) or []
        policies = [_summary({"kind": "CiliumClusterwideNetworkPolicy", **item}) for item in items]
        return {"policies": policies, "count": len(policies)}

    async def get_flows(
        self,
        namespace: str | None = None,
        pod: str | None = None,
        verdict: str | None = None,
        since: str | None = None,
        last: int = 100,
    ) -> dict[str, Any]:
        selection = FlowFilter(namespace=namespace, pod=pod, verdict=verdict, since=since, last=last)
        flows = await self.flow_source.flows(selection)
        dropped = sum(1 for flow in flows if flow["verdict"] == "DROPPED")
        return {"flows": flows, "count": len(flows), "dropped": dropped}

    async def executer(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        if operation not in {o.name for o in self.operations}:
            raise AdapterError(f"cilium has no operation {operation}")
        try:
            result: dict[str, Any] = await getattr(self, operation)(**params)
        except TypeError as error:
            raise AdapterError(f"cilium {operation}: {error}") from error
        return result

    async def test(self) -> dict[str, Any]:
        await self.kubernetes.get(f"{CILIUM_API}/ciliumclusterwidenetworkpolicies?limit=1")
        return {"ok": True, "kubernetes_url": self.kubernetes.base_url}

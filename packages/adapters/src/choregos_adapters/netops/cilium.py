# SPDX-License-Identifier: Apache-2.0
"""Cilium: its network policies through the Kubernetes API, its flows through Hubble (ADR 0052).

Read-only BY CONSTRUCTION, twice:
- the Kubernetes client below has `get` and nothing else — there is no code path that sends a
  verb other than GET, so no configuration mistake turns it into a `kubectl apply`;
- the token it presents belongs to a dedicated service account whose ClusterRole grants `get`,
  `list` on `ciliumnetworkpolicies` and `ciliumclusterwidenetworkpolicies` only (ADR 0052 gives the
  manifest). The connector never falls back to the pod's own service account: a platform pod's
  token is wider than what this connector reads.

A policy change is never applied here: `propose_network_policy` (S33-09) offers it as a pull
request to the GitOps repository — a governed action a person approves —, and Argo CD applies what
the release train merged (ADR 0006, ADR 0009).
"""

from __future__ import annotations

from typing import Any

import httpx
import yaml

from ..errors import AdapterError, UpstreamError
from ..registry import OperationSpec
from .gitops import GitOpsTarget, propose_change
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


POLICY_KINDS = frozenset({"CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy"})

CILIUM_WRITE_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "propose_network_policy",
        "write",
        "proposes a CiliumNetworkPolicy (or clusterwide) as a pull request to the GitOps repository; the "
        "release train applies it once merged — nothing is applied to the cluster",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "the file, under the connector's GitOps root"},
                "manifest": {"type": "string", "description": "the policy's YAML, whole"},
                "title": {"type": "string", "maxLength": 200},
                "rationale": {"type": "string", "description": "why: the findings and the risk"},
            },
            "required": ["path", "manifest", "title", "rationale"],
            "additionalProperties": False,
        },
        human_approval=True,
        platform_arguments=("action_key", "work_item_key"),
    ),
)


def checked_policy(manifest: str) -> dict[str, Any]:
    """A manifest that is ONE Cilium policy, named — anything else never reaches a pull request."""
    try:
        documents = [d for d in yaml.safe_load_all(manifest) if d is not None]
    except yaml.YAMLError as error:
        raise AdapterError(f"cilium: the manifest is not YAML: {error}") from error
    if len(documents) != 1 or not isinstance(documents[0], dict):
        raise AdapterError("cilium: the manifest must hold exactly one policy")
    policy = documents[0]
    if policy.get("apiVersion") != "cilium.io/v2" or policy.get("kind") not in POLICY_KINDS:
        raise AdapterError(
            f"cilium: only {', '.join(sorted(POLICY_KINDS))} of cilium.io/v2 are proposed here"
        )
    if not (policy.get("metadata") or {}).get("name"):
        raise AdapterError("cilium: the policy has no metadata.name")
    return policy


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
    operations: tuple[OperationSpec, ...] = CILIUM_READ_OPERATIONS + CILIUM_WRITE_OPERATIONS

    def __init__(
        self, kubernetes: ReadOnlyKubernetes, flows: FlowSource, gitops: GitOpsTarget | None = None
    ) -> None:
        self.kubernetes = kubernetes
        self.flow_source = flows
        self.gitops = gitops

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        scm: Any = None,
    ) -> Any:
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
        gitops = (
            GitOpsTarget(
                scm,
                str(config["gitops_repo"]),
                str(config.get("gitops_base") or "main"),
                str(config.get("gitops_path") or "cilium"),
            )
            if scm is not None and config.get("gitops_repo")
            else None
        )
        return cls(kubernetes, hubble, gitops)

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

    async def propose_network_policy(
        self,
        path: str,
        manifest: str,
        title: str,
        rationale: str,
        action_key: str,
        work_item_key: str | None = None,
    ) -> dict[str, Any]:
        """A pull request with the policy, under the GitOps root; the cluster is not touched."""
        from .nginx import confined

        if self.gitops is None:
            raise AdapterError(
                "cilium: no GitOps repository configured (`gitops_repo`): nothing to propose to"
            )
        target = confined(self.gitops.root, path, service="cilium")
        if not target.endswith((".yaml", ".yml")):
            raise AdapterError("cilium: a policy file ends in .yaml or .yml")
        policy = checked_policy(manifest)
        result = await propose_change(
            self.gitops,
            files={target: manifest if manifest.endswith("\n") else manifest + "\n"},
            title=title,
            rationale=rationale,
            action_key=action_key,
            work_item_key=work_item_key,
        )
        return {**result, "policy": f"{policy['kind']}/{policy['metadata']['name']}"}

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

# SPDX-License-Identifier: Apache-2.0
"""The netops fakes (ADR 0005, ADR 0052), scriptable, each speaking the protocol of the real thing
so that the REAL connector is what the tests exercise:

- `FakePfSense` answers the pfrest v2 API (`X-API-Key`, the `{code, status, data}` envelope);
- `FakeCiliumKubernetes` answers the Kubernetes API for the two Cilium policy kinds, and records
  every request — a test can prove that nothing but GET ever left the connector;
- the Hubble side replays a RECORDED trace (`recordings/*.jsonl`, the output of
  `hubble observe -o jsonpb`) through `RecordedFlows`;
- `FakeRepo` holds the files of a GitOps repository, as the forge's API would serve them.

The default scene is the one the netops eval reads: in `shop`, `frontend` calls `api` on 8080/TCP
and is dropped, because the policy `api-ingress` selects `api` and admits only `gateway`.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..netops.hubble import RecordedFlows

RECORDINGS = Path(__file__).parent / "recordings"
DROPPED_FLOW_TRACE = RECORDINGS / "hubble-dropped-flow.jsonl"


def _envelope(data: Any, code: int = 200, response_id: str = "SUCCESS", message: str = "") -> httpx.Response:
    status = "ok" if code < 400 else "error"
    return httpx.Response(
        code,
        json={"code": code, "status": status, "response_id": response_id, "message": message, "data": data},
    )


def _default_rules() -> list[dict[str, Any]]:
    return [
        {
            "id": 0,
            "tracker": 1700000101,
            "type": "pass",
            "interface": ["lan"],
            "ipprotocol": "inet",
            "protocol": "tcp",
            "source": "lan",
            "destination": "any",
            "destination_port": "443",
            "descr": "LAN to any HTTPS",
            "disabled": False,
        },
        {
            "id": 1,
            "tracker": 1700000102,
            "type": "pass",
            "interface": ["wan"],
            "ipprotocol": "inet",
            "protocol": "tcp",
            "source": "any",
            "destination": "wan:ip",
            "destination_port": "8443",
            "descr": "WAN to the reverse proxy",
            "disabled": False,
        },
        {
            "id": 2,
            "tracker": 1700000103,
            "type": "block",
            "interface": ["wan"],
            "ipprotocol": "inet",
            "protocol": "any",
            "source": "any",
            "destination": "any",
            "descr": "Default deny on WAN",
            "disabled": False,
        },
    ]


@dataclass
class FakePfSense:
    """A pfSense with the REST API package. Rules are kept in evaluation order; `id` is the index,
    as in pfSense, so it moves when a rule before it goes — `tracker` is the stable identity."""

    #: the key it expects in `X-API-Key`; empty: any non-empty key (the demo's default)
    api_key: str = field(default="", repr=False)
    rules: list[dict[str, Any]] = field(default_factory=_default_rules)
    aliases: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "id": 0,
                "name": "web_servers",
                "type": "host",
                "address": ["10.10.0.11", "10.10.0.12"],
                "descr": "",
            },
            {"id": 1, "name": "admin_ports", "type": "port", "address": ["22", "8443"], "descr": ""},
        ]
    )
    port_forwards: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "id": 0,
                "interface": "wan",
                "protocol": "tcp",
                "destination": "wan:ip",
                "destination_port": "443",
                "target": "10.10.0.11",
                "local_port": "8443",
                "descr": "HTTPS to the reverse proxy",
            }
        ]
    )
    interfaces: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": "wan", "if": "vtnet0", "descr": "WAN", "enable": True, "typev4": "dhcp"},
            {
                "id": "lan",
                "if": "vtnet1",
                "descr": "LAN",
                "enable": True,
                "ipaddr": "10.10.0.1",
                "subnet": 24,
            },
        ]
    )
    gateways: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"name": "WAN_DHCP", "srcip": "203.0.113.10", "monitorip": "203.0.113.1", "delay": "4.1ms",
             "stddev": "0.6ms", "loss": "0.0%", "status": "online", "substatus": "none"},
        ]
    )  # fmt: skip
    #: (method, path) of every request, in order.
    received: list[tuple[str, str]] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.answer)

    def state(self) -> list[dict[str, Any]]:
        """The rules as they are, copied: what a test compares before and after."""
        return copy.deepcopy(self.rules)

    def _renumber(self) -> None:
        for index, rule in enumerate(self.rules):
            rule["id"] = index

    def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v2")
        self.received.append((request.method, path))
        presented = request.headers.get("x-api-key") or ""
        if not presented or (self.api_key and presented != self.api_key):
            return _envelope(None, 401, "AUTH_AUTHENTICATION_FAILED", "Authentication failed")
        if request.method == "GET":
            return self._read(path, dict(request.url.params))
        return self._write(request.method, path, request)

    def _read(self, path: str, params: dict[str, str]) -> httpx.Response:
        collections: dict[str, list[dict[str, Any]]] = {
            "/firewall/rules": self.rules,
            "/firewall/aliases": self.aliases,
            "/firewall/nat/port_forwards": self.port_forwards,
            "/interfaces": self.interfaces,
            "/status/gateways": self.gateways,
        }
        if path not in collections:
            return _envelope(None, 404, "ENDPOINT_NOT_FOUND", f"no endpoint {path}")
        items = copy.deepcopy(collections[path])
        limit = params.pop("limit", None)
        for key, value in params.items():
            if key.endswith("__contains"):
                name = key.removesuffix("__contains")
                items = [i for i in items if value in (i.get(name) or []) or value in str(i.get(name, ""))]
            else:
                items = [i for i in items if str(i.get(key)) == value]
        if limit:
            items = items[: int(limit)]
        return _envelope(items)

    def _write(self, method: str, path: str, request: httpx.Request) -> httpx.Response:
        """Writes arrive with S33-09 (governed actions); until then the fake refuses them, named."""
        return _envelope(None, 405, "METHOD_NOT_ALLOWED", f"{method} {path} is not served by this fake")


CILIUM_POLICIES: list[dict[str, Any]] = [
    {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": {
            "name": "api-ingress",
            "namespace": "shop",
            "labels": {"app.kubernetes.io/managed-by": "argocd"},
            "managedFields": [{"manager": "argocd-controller"}],
        },
        "spec": {
            "endpointSelector": {"matchLabels": {"app": "api"}},
            "ingress": [
                {
                    "fromEndpoints": [{"matchLabels": {"app": "gateway"}}],
                    "toPorts": [{"ports": [{"port": "8080", "protocol": "TCP"}]}],
                }
            ],
        },
    },
    {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": {"name": "api-egress", "namespace": "shop"},
        "spec": {
            "endpointSelector": {"matchLabels": {"app": "api"}},
            "egress": [
                {
                    "toEndpoints": [{"matchLabels": {"app": "postgres"}}],
                    "toPorts": [{"ports": [{"port": "5432", "protocol": "TCP"}]}],
                },
                {
                    "toEndpoints": [{"matchLabels": {"k8s:io.kubernetes.pod.namespace": "kube-system"}}],
                    "toPorts": [{"ports": [{"port": "53", "protocol": "ANY"}]}],
                },
            ],
        },
    },
]

CILIUM_CLUSTERWIDE_POLICIES: list[dict[str, Any]] = [
    {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumClusterwideNetworkPolicy",
        "metadata": {"name": "allow-dns"},
        "spec": {
            "endpointSelector": {},
            "egress": [
                {
                    "toEndpoints": [{"matchLabels": {"k8s:k8s-app": "kube-dns"}}],
                    "toPorts": [{"ports": [{"port": "53", "protocol": "ANY"}]}],
                }
            ],
        },
    }
]

_NAMESPACED = re.compile(r"/apis/cilium\.io/v2/namespaces/([^/]+)/ciliumnetworkpolicies")


@dataclass
class FakeCiliumKubernetes:
    """The Kubernetes API, for Cilium's two policy kinds. It answers GET; any other verb is a 405
    AND a trace in `received` — the proof that the connector never tried."""

    #: the bearer it expects; empty: any (the demo's default)
    token: str = field(default="", repr=False)
    policies: list[dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(CILIUM_POLICIES))
    clusterwide: list[dict[str, Any]] = field(
        default_factory=lambda: copy.deepcopy(CILIUM_CLUSTERWIDE_POLICIES)
    )
    received: list[tuple[str, str]] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.answer)

    def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.received.append((request.method, path))
        bearer = (request.headers.get("authorization") or "").removeprefix("Bearer ")
        if not bearer or (self.token and bearer != self.token):
            return httpx.Response(401, json={"kind": "Status", "reason": "Unauthorized"})
        if request.method != "GET":
            return httpx.Response(405, json={"kind": "Status", "reason": "MethodNotAllowed"})
        if path == "/apis/cilium.io/v2/ciliumclusterwidenetworkpolicies":
            return httpx.Response(
                200, json={"kind": "CiliumClusterwideNetworkPolicyList", "items": self.clusterwide}
            )
        if path == "/apis/cilium.io/v2/ciliumnetworkpolicies":
            return httpx.Response(200, json={"kind": "CiliumNetworkPolicyList", "items": self.policies})
        if match := _NAMESPACED.fullmatch(path):
            items = [p for p in self.policies if p["metadata"].get("namespace") == match.group(1)]
            return httpx.Response(200, json={"kind": "CiliumNetworkPolicyList", "items": items})
        return httpx.Response(404, json={"kind": "Status", "reason": "NotFound"})


def recorded_flows(path: Path = DROPPED_FLOW_TRACE) -> RecordedFlows:
    return RecordedFlows.from_file(path)


NGINX_FILES: dict[str, str] = {
    "nginx/nginx.conf": (
        "worker_processes auto;\n"
        "events { worker_connections 1024; }\n"
        "http {\n"
        "    include /etc/nginx/mime.types;\n"
        "    include conf.d/*.conf;\n"
        "}\n"
    ),
    "nginx/conf.d/shop.conf": (
        "upstream shop_api { server api.shop.svc.cluster.local:8080; }\n"
        "server {\n"
        "    listen 8443 ssl;\n"
        "    server_name shop.example.org;\n"
        "    ssl_certificate /etc/nginx/tls/tls.crt;\n"
        "    ssl_certificate_key /etc/nginx/tls/tls.key;\n"
        "    location /api/ { proxy_pass http://shop_api; }\n"
        "}\n"
    ),
}


@dataclass
class FakeRepo:
    """The files of a GitOps repository at its default branch, as the forge serves them."""

    files: dict[str, str] = field(default_factory=lambda: dict(NGINX_FILES))
    reads: list[str] = field(default_factory=list)

    async def list_files(self, prefix: str) -> list[str]:
        return sorted(path for path in self.files if path.startswith(prefix))

    async def read_file(self, path: str) -> str:
        from ..errors import AdapterError

        self.reads.append(path)
        if path not in self.files:
            raise AdapterError(f"nginx: no file {path}")
        return self.files[path]

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
import datetime as dt
import hashlib
import json
import random
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
    #: how many writes reached it — the proof that an unapproved one did not
    writes: int = 0

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
        """The singular endpoint `/firewall/rule`: POST (with `placement`), PATCH (by `id`), DELETE
        (`?id=`). Every write counts in `writes`; `apply` is recorded as pfSense would apply it."""
        if path != "/firewall/rule":
            return _envelope(None, 405, "METHOD_NOT_ALLOWED", f"{method} {path} is not served by this fake")
        self.writes += 1
        if method == "DELETE":
            index = int(request.url.params.get("id", "-1"))
            if not 0 <= index < len(self.rules):
                return _envelope(None, 404, "MODEL_OBJECT_NOT_FOUND", f"no rule {index}")
            removed = self.rules.pop(index)
            self._renumber()
            return _envelope(removed)
        body = json.loads(request.content or b"{}")
        body.pop("apply", None)
        if method == "PATCH":
            index = int(body.pop("id", -1))
            if not 0 <= index < len(self.rules):
                return _envelope(None, 404, "MODEL_OBJECT_NOT_FOUND", f"no rule {index}")
            self.rules[index].update(body)
            return _envelope(copy.deepcopy(self.rules[index]))
        placement = body.pop("placement", None)
        tracker = max((int(r["tracker"]) for r in self.rules), default=1700000100) + 1
        rule = {**body, "tracker": tracker}
        self.rules.insert(len(self.rules) if placement is None else int(placement), rule)
        self._renumber()
        return _envelope(copy.deepcopy(rule))


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


# ───────────────────────── the scenes of the tool-script matrix (S57-09) ─────────────────────────
#
# Two larger datasets, from the examples page of PLAN-2026-11-03-tool-scripts: a firewall whose SSH
# exposure needs three reads JOINED through aliases (E1), and a cluster whose dropped flows need one
# read per namespace, then an aggregation (E2). Built by code, deterministically: the same rules and
# flows on every machine. `fakes/scenes.py` wires them to the real connectors.

#: The interfaces of the SSH-exposure firewall and how many rules each one has, in evaluation order.
SSH_EXPOSURE_INTERFACES: tuple[tuple[str, str, str, int], ...] = (
    ("wan", "vtnet0", "WAN", 30),
    ("lan", "vtnet1", "LAN", 96),
    ("opt1", "vtnet2", "DMZ", 20),
    ("opt2", "vtnet3", "PARTNERS", 12),
    ("opt3", "vtnet4", "GUEST", 14),
    ("opt4", "vtnet5", "MGMT", 12),
)
#: The rules that expose TCP 22 to any source, by `<interface>#<position>` (1-based, per interface).
SSH_EXPOSED = ("wan#17", "opt2#4", "lan#88")
#: Rules that look like an exposure and are not one: another port, a block, a disabled rule, a
#: restricted source, a protocol without ports.
SSH_DECOYS = ("wan#3", "wan#30", "lan#2", "lan#5", "opt3#7", "opt4#2")


def _rule(interface: str, position: int, **fields: Any) -> dict[str, Any]:
    return {
        "type": "pass",
        "interface": [interface],
        "ipprotocol": "inet",
        "protocol": "tcp",
        "source": interface,
        "destination": "any",
        "destination_port": "443",
        "descr": f"{interface} rule {position}",
        "disabled": False,
        **fields,
    }


#: What each special rule of the SSH-exposure firewall is, by its label.
_SSH_SPECIAL: dict[str, dict[str, Any]] = {
    "wan#3": {"source": "any", "destination": "wan:ip", "destination_port": "2222", "descr": "SFTP gateway"},
    "wan#17": {"source": "any", "destination": "wan:ip", "destination_port": "22",
               "descr": "Temporary vendor SSH"},
    "wan#30": {"type": "block", "protocol": "any", "source": "any", "destination_port": "",
               "descr": "Default deny on WAN"},
    "lan#2": {"protocol": "icmp", "source": "any", "destination_port": "", "descr": "Ping from anywhere"},
    "lan#5": {"source": "lan", "destination_port": "22", "descr": "LAN administrators to SSH"},
    "lan#88": {"source": "any", "destination": "lan:ip", "destination_port": "MGMT_PORTS",
               "descr": "Management from anywhere"},
    "opt2#4": {"source": "PARTNERS", "destination": "opt2:ip", "destination_port": "22",
               "descr": "Partner bastion"},
    "opt3#7": {"source": "any", "destination_port": "22", "disabled": True, "descr": "Guest SSH (retired)"},
    "opt4#2": {"source": "MGMT_NETS", "destination_port": "22", "descr": "Management network to SSH"},
}  # fmt: skip


def _filler(interface: str, position: int, hosts: int) -> dict[str, Any]:
    """An ordinary rule: the interface's own network or a host alias as the source, or any source
    on a port that is not SSH. None of them exposes TCP 22 to any source."""
    shape = position % 6
    if shape == 0:
        return _rule(interface, position, source="any", destination_port="WEB_PORTS",
                     descr=f"{interface} web {position}")  # fmt: skip
    if shape == 1:
        return _rule(interface, position, protocol="udp", source="any", destination_port="DNS_PORTS",
                     descr=f"{interface} DNS {position}")  # fmt: skip
    if shape == 2:
        alias = f"HOSTS_{(position + len(interface)) % hosts + 1:02d}"
        return _rule(interface, position, source=alias, destination_port="ADMIN_PORTS",
                     descr=f"{interface} administration from {alias}")  # fmt: skip
    if shape == 3:
        return _rule(interface, position, destination_port="80", descr=f"{interface} HTTP out {position}")
    if shape == 4:
        alias = f"HOSTS_{(position * 7) % hosts + 1:02d}"
        return _rule(interface, position, source=alias, destination_port="8443",
                     descr=f"{interface} reverse proxy from {alias}")  # fmt: skip
    return _rule(interface, position, descr=f"{interface} HTTPS out {position}")


def ssh_exposure_firewall() -> FakePfSense:
    """E1: 6 interfaces, 184 rules, 41 aliases; three rules expose TCP 22 to any source — `wan#17`
    (source `any`), `opt2#4` (source `PARTNERS`, an alias holding the alias `PARTNER_CLOUD`, which holds
    `0.0.0.0/0`) and `lan#88` (destination port `MGMT_PORTS`, an alias holding 22) — and six decoys
    (`SSH_DECOYS`). A rule is named `<interface>#<position>`, its position among its interface's rules,
    from 1; pfSense's `id` is a position in the whole list."""
    hosts = 34
    aliases: list[dict[str, Any]] = [
        {"name": "PARTNERS", "type": "network", "address": ["198.51.100.0/24", "PARTNER_CLOUD"],
         "descr": "Partner networks"},
        {"name": "PARTNER_CLOUD", "type": "network", "address": ["0.0.0.0/0"],
         "descr": "Partner cloud egress (whole internet, temporary)"},
        {"name": "MGMT_PORTS", "type": "port", "address": ["22", "443", "8443"], "descr": "Management ports"},
        {"name": "MGMT_NETS", "type": "network", "address": ["10.40.0.0/24"], "descr": "Management network"},
        {"name": "WEB_PORTS", "type": "port", "address": ["80", "443"], "descr": "Web"},
        {"name": "DNS_PORTS", "type": "port", "address": ["53"], "descr": "DNS"},
        {"name": "ADMIN_PORTS", "type": "port", "address": ["22", "3389"], "descr": "Administration"},
    ]  # fmt: skip
    aliases += [
        {
            "name": f"HOSTS_{n:02d}",
            "type": "host",
            "address": [f"10.{20 + n // 10}.{n % 10}.{10 + i}" for i in range(1 + n % 3)],
            "descr": f"Office hosts {n}",
        }
        for n in range(1, hosts + 1)
    ]
    for index, alias in enumerate(aliases):
        alias["id"] = index
    rules: list[dict[str, Any]] = []
    for interface, _device, _descr, count in SSH_EXPOSURE_INTERFACES:
        for position in range(1, count + 1):
            label = f"{interface}#{position}"
            special = _SSH_SPECIAL.get(label)
            rules.append(
                _rule(interface, position, **special) if special else _filler(interface, position, hosts)
            )
    for index, rule in enumerate(rules):
        rule["id"] = index
        rule["tracker"] = 1700001000 + index
    interfaces = [
        {"id": name, "if": device, "descr": descr, "enable": True,
         **({"typev4": "dhcp"} if name == "wan" else {"ipaddr": f"10.{10 * n}.0.1", "subnet": 24})}
        for n, (name, device, descr, _) in enumerate(SSH_EXPOSURE_INTERFACES)
    ]  # fmt: skip
    return FakePfSense(rules=rules, aliases=aliases, interfaces=interfaces)


#: The namespaces of the dropped-flows cluster.
DROPPED_FLOWS_NAMESPACES = (
    "shop", "payments", "auth", "catalog", "search", "orders",
    "inventory", "shipping", "notifications", "analytics", "monitoring", "ingress",
)  # fmt: skip
#: (source, destination, port, dropped flows, the policy that drops them): the top five first.
DROPPED_FLOWS_PAIRS: tuple[tuple[str, str, int, int, str], ...] = (
    ("shop/frontend", "payments/api", 8443, 42, "payments-ingress"),
    ("orders/worker", "inventory/api", 9090, 31, "inventory-ingress"),
    ("search/indexer", "catalog/db", 5432, 24, "catalog-db-ingress"),
    ("notifications/sender", "auth/api", 8080, 17, "auth-ingress"),
    ("analytics/collector", "monitoring/grafana", 3000, 9, "grafana-ingress"),
    ("monitoring/prometheus", "shipping/api", 9100, 4, "shipping-ingress"),
    ("ingress/nginx", "search/api", 9200, 3, "search-ingress"),
    ("shipping/api", "orders/api", 8080, 2, "orders-ingress"),
)
#: (source, destination, port, forwarded flows): what is allowed, 2,268 flows.
_FORWARDED_PAIRS: tuple[tuple[str, str, int, int], ...] = (
    ("ingress/nginx", "shop/frontend", 3000, 698),
    ("shop/frontend", "shop/api", 8080, 410),
    ("payments/checkout", "payments/api", 8443, 230),
    ("shop/api", "orders/api", 8080, 190),
    ("orders/api", "inventory/api", 9090, 160),
    ("search/api", "catalog/db", 5432, 150),
    ("shop/api", "auth/api", 8080, 140),
    ("monitoring/prometheus", "shop/api", 9100, 120),
    ("orders/api", "shipping/api", 8080, 90),
    ("notifications/api", "notifications/sender", 8080, 80),
)
DROPPED_FLOWS_START = "2026-11-04T09:00:00Z"


def _hubble_endpoint(where: str, number: int) -> dict[str, Any]:
    namespace, app = where.split("/")
    return {
        "namespace": namespace,
        "pod_name": f"{app}-{_suffix(where)}-{number % 3}",
        "labels": [f"k8s:app={app}", f"k8s:io.kubernetes.pod.namespace={namespace}"],
        "identity": 10000 + sum(map(ord, where)) % 5000,
    }


def _suffix(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:9]


def _hubble_line(index: int, source: str, destination: str, port: int, policy: str | None) -> dict[str, Any]:
    start = dt.datetime.fromisoformat(DROPPED_FLOWS_START.replace("Z", "+00:00"))
    when = (start + dt.timedelta(seconds=index * 1.5)).isoformat().replace("+00:00", "Z")
    flow: dict[str, Any] = {
        "time": when,
        "verdict": "DROPPED" if policy else "FORWARDED",
        "traffic_direction": "INGRESS",
        "IP": {"source": f"10.0.{index % 250}.{index % 7 + 10}", "destination": f"10.1.{index % 200}.20"},
        "l4": {"TCP": {"source_port": 40000 + index % 20000, "destination_port": port}},
        "source": _hubble_endpoint(source, index),
        "destination": _hubble_endpoint(destination, index),
        "node_name": f"worker-{index % 4}",
        "Summary": f"TCP Flags: SYN{' (denied)' if policy else ''}",
    }
    if policy:
        flow["drop_reason_desc"] = "POLICY_DENIED"
        flow["ingress_denied_by"] = [{"name": policy, "namespace": destination.split("/", maxsplit=1)[0]}]
    return {"flow": flow, "node_name": flow["node_name"], "time": when}


def dropped_flows_trace() -> RecordedFlows:
    """E2: 2,400 flows over one hour across 12 namespaces, 132 of them DROPPED; the top pair is
    `shop/frontend → payments/api:8443` (42 drops, policy `payments-ingress`), then four smaller ones.
    The order is a fixed shuffle, so a pair's flows are spread over the hour.

    Sized for the platform's bound on a tool result (64 KiB, text and `structuredContent` together):
    no namespace's dropped flows exceed it, so one `get_flows` per namespace reads them whole. The
    examples page first said 310 drops and 142 for the top pair; at that size, the busiest namespaces'
    answers are cut."""
    kinds: list[tuple[str, str, int, str | None]] = []
    for source, destination, port, count, policy in DROPPED_FLOWS_PAIRS:
        kinds += [(source, destination, port, policy)] * count
    for source, destination, port, count in _FORWARDED_PAIRS:
        kinds += [(source, destination, port, None)] * count
    random.Random(57).shuffle(kinds)
    return RecordedFlows([_hubble_line(i, *kind) for i, kind in enumerate(kinds)])


def _ingress_policy(namespace: str, name: str, app: str, peer: str, port: int) -> dict[str, Any]:
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/managed-by": "argocd"},
        },
        "spec": {
            "endpointSelector": {"matchLabels": {"app": app}},
            "ingress": [
                {
                    "fromEndpoints": [{"matchLabels": {"app": peer}}],
                    "toPorts": [{"ports": [{"port": str(port), "protocol": "TCP"}]}],
                }
            ],
        },
    }


def dropped_flows_cluster() -> FakeCiliumKubernetes:
    """E2's policies: each one selects the destination of a dropped pair and admits another peer."""
    return FakeCiliumKubernetes(
        policies=[
            _ingress_policy("payments", "payments-ingress", "api", "checkout", 8443),
            _ingress_policy("inventory", "inventory-ingress", "api", "api", 9090),
            _ingress_policy("catalog", "catalog-db-ingress", "db", "api", 5432),
            _ingress_policy("auth", "auth-ingress", "api", "api", 8080),
            _ingress_policy("monitoring", "grafana-ingress", "grafana", "nginx", 3000),
            _ingress_policy("shipping", "shipping-ingress", "api", "api", 8080),
            _ingress_policy("search", "search-ingress", "api", "api", 9200),
            _ingress_policy("orders", "orders-ingress", "api", "api", 8080),
        ],
        clusterwide=[],
    )

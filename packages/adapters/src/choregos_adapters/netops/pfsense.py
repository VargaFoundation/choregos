# SPDX-License-Identifier: Apache-2.0
"""pfSense through its REST API package (pfrest, `pfSense-pkg-RESTAPI`, v2) — ADR 0052.

Reads only: firewall rules, aliases, NAT port forwards, interfaces, gateway status. Every answer of
the v2 API is an envelope `{code, status, response_id, message, data}`; the connector hands the
agent `data`, nothing of the envelope and never the key.

Authentication is an API key in `X-API-Key`: a key carries the privileges of the pfSense user who
generated it, so the key given to Choregos belongs to a user whose privileges are the pages this
connector reads (`WebCfg - Firewall: Rules`, `… Aliases`, `… NAT: Port Forward`, `Status: Gateways`,
`Interfaces`) and nothing else. TLS is verified (`tls.py`).
"""

from __future__ import annotations

from typing import Any

import httpx

from ..errors import AdapterError
from ..http.rest import RestClient
from ..registry import OperationSpec
from .tls import tls_verification

_TEXT: dict[str, Any] = {"type": "string"}
_LIMIT: dict[str, Any] = {"type": "integer", "minimum": 1, "maximum": 1000}


def _schema(**properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": [], "additionalProperties": False}


#: What the type declares to the registry: reads, allowed by default (ADR 0034 §4).
PFSENSE_READ_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "list_firewall_rules",
        "read",
        "lists the firewall rules in their evaluation order, optionally for one interface",
        _schema(interface=_TEXT, limit=_LIMIT),
    ),
    OperationSpec(
        "list_aliases",
        "read",
        "lists the firewall aliases (hosts, networks, ports), optionally one by name",
        _schema(name=_TEXT),
    ),
    OperationSpec(
        "list_port_forwards",
        "read",
        "lists the NAT port forwards, optionally for one interface",
        _schema(interface=_TEXT),
    ),
    OperationSpec("list_interfaces", "read", "lists the interfaces and their addresses", _schema()),
    OperationSpec(
        "gateway_status", "read", "reads the status of each gateway: loss, delay, state", _schema()
    ),
)


class PfSenseFirewall:
    """A pfSense reached by its REST API. `transport` is the seam the fake plugs into."""

    service = "pfsense"
    operations: tuple[OperationSpec, ...] = PFSENSE_READ_OPERATIONS

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        verify: Any = True,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        if not api_key:
            raise AdapterError("pfsense: no API key (`api_key`, written as a reference)")
        self.base_url = base_url.rstrip("/")
        self.tls_verified = verify is not False
        client = httpx.AsyncClient(timeout=timeout, verify=verify, transport=transport)
        self._rest = RestClient(
            f"{self.base_url}/api/v2",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            client=client,
            service=self.service,
        )

    @classmethod
    def from_config(cls, config: dict[str, Any], *, transport: httpx.AsyncBaseTransport | None = None) -> Any:
        return cls(
            base_url=str(config["base_url"]),
            api_key=str(config.get("api_key") or ""),
            verify=tls_verification("pfsense", config),
            transport=transport,
        )

    async def _data(self, method: str, path: str, **kwargs: Any) -> Any:
        body = await self._rest.request(method, path, **kwargs)
        if not isinstance(body, dict) or "data" not in body:
            raise AdapterError(f"pfsense: {method} {path} answered without a `data` envelope")
        return body["data"]

    # ───────────────────────────── reads ─────────────────────────────

    async def list_firewall_rules(
        self, interface: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if interface:
            # `interface` is an array in v2: `contains` is membership (pfrest query filters).
            params["interface__contains"] = interface
        if limit:
            params["limit"] = limit
        rules = await self._data("GET", "/firewall/rules", params=params)
        return {"rules": rules, "count": len(rules)}

    async def list_aliases(self, name: str | None = None) -> dict[str, Any]:
        aliases = await self._data("GET", "/firewall/aliases", params={"name": name} if name else {})
        return {"aliases": aliases, "count": len(aliases)}

    async def list_port_forwards(self, interface: str | None = None) -> dict[str, Any]:
        params = {"interface": interface} if interface else {}
        forwards = await self._data("GET", "/firewall/nat/port_forwards", params=params)
        return {"port_forwards": forwards, "count": len(forwards)}

    async def list_interfaces(self) -> dict[str, Any]:
        return {"interfaces": await self._data("GET", "/interfaces")}

    async def gateway_status(self) -> dict[str, Any]:
        return {"gateways": await self._data("GET", "/status/gateways")}

    # ───────────────────────────── the registry's seam ─────────────────────────────

    async def executer(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        """A declared operation, by name: what the broker and the `connector.call` effect call."""
        if operation not in {o.name for o in self.operations}:
            raise AdapterError(f"pfsense has no operation {operation}")
        try:
            result: dict[str, Any] = await getattr(self, operation)(**params)
        except TypeError as error:
            raise AdapterError(f"pfsense {operation}: {error}") from error
        return result

    async def test(self) -> dict[str, Any]:
        await self._data("GET", "/status/gateways")
        return {"ok": True, "base_url": self.base_url, "tls_verified": self.tls_verified}

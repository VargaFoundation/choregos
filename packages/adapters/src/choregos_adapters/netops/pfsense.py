# SPDX-License-Identifier: Apache-2.0
"""pfSense through its REST API package (pfrest, `pfSense-pkg-RESTAPI`, v2) — ADR 0052.

Reads: firewall rules, aliases, NAT port forwards, interfaces, gateway status. Writes (S33-09): a
firewall rule created, updated, deleted or restored — each one a governed action a PERSON approves
(`human_approval`), never a direct call, and each one undone by its declared compensation. Every answer of
the v2 API is an envelope `{code, status, response_id, message, data}`; the connector hands the
agent `data`, nothing of the envelope and never the key.

Authentication is an API key in `X-API-Key`: a key carries the privileges of the pfSense user who
generated it, so the key given to Choregos belongs to a user whose privileges are the pages this
connector reads (`WebCfg - Firewall: Rules`, `… Aliases`, `… NAT: Port Forward`, `Status: Gateways`,
`Interfaces`) and nothing else. TLS is verified (`tls.py`).
"""

from __future__ import annotations

import hashlib
import json
import re
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


_TRACKER: dict[str, Any] = {
    "type": ["string", "integer"],
    "description": "the rule's `tracker`, its stable identity (its `id` is a position and moves)",
}
_RULE: dict[str, Any] = {
    "type": "object",
    "description": "pfrest v2 rule fields: type, interface, ipprotocol, protocol, source, destination, "
    "destination_port, descr…",
}


def _write_schema(required: tuple[str, ...], **properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


#: The writes (S33-09): always a person's decision, always undone by what they declare.
PFSENSE_WRITE_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "create_firewall_rule",
        "write",
        "creates a firewall rule and applies it (once per action: a replay finds it)",
        _write_schema(("rule",), rule=_RULE),
        human_approval=True,
        compensation={"operation": "delete_firewall_rule", "arguments": {"tracker": "{{ result.tracker }}"}},
        platform_arguments=("action_key",),
    ),
    OperationSpec(
        "update_firewall_rule",
        "write",
        "changes fields of a firewall rule and applies it; the previous rule is kept to restore it",
        _write_schema(("tracker", "changes"), tracker=_TRACKER, changes=_RULE),
        human_approval=True,
        compensation={
            "operation": "restore_firewall_rule",
            "arguments": {"snapshot": "{{ result.snapshot }}"},
        },
        platform_arguments=("action_key",),
    ),
    OperationSpec(
        "delete_firewall_rule",
        "write",
        "deletes a firewall rule and applies it; the rule and its position are kept to restore it",
        _write_schema(("tracker",), tracker=_TRACKER),
        human_approval=True,
        compensation={
            "operation": "restore_firewall_rule",
            "arguments": {"snapshot": "{{ result.snapshot }}"},
        },
    ),
    OperationSpec(
        "restore_firewall_rule",
        "write",
        "puts a rule back as a snapshot recorded it, at its position (compensates an update or a delete)",
        _write_schema(("snapshot",), snapshot={"type": "string"}),
        human_approval=True,
    ),
)

#: The marker a rule written by an action carries in its description: what makes a replay find it.
_MARKER = re.compile(r"\s*\[choregos:[0-9a-f]+\]")


def marker(action_key: str) -> str:
    return f"[choregos:{hashlib.sha256(action_key.encode()).hexdigest()[:12]}]"


class PfSenseFirewall:
    """A pfSense reached by its REST API. `transport` is the seam the fake plugs into."""

    service = "pfsense"
    operations: tuple[OperationSpec, ...] = PFSENSE_READ_OPERATIONS + PFSENSE_WRITE_OPERATIONS

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

    # ───────────────────────────── writes (governed actions) ─────────────────────────────

    async def _by_tracker(self, tracker: str | int) -> dict[str, Any] | None:
        found = await self._data("GET", "/firewall/rules", params={"tracker": str(tracker)})
        return dict(found[0]) if found else None

    async def create_firewall_rule(self, rule: dict[str, Any], action_key: str) -> dict[str, Any]:
        """Idempotent by the action's marker: a replay after a lost answer finds the rule it made."""
        tag = marker(action_key)
        existing = await self._data("GET", "/firewall/rules", params={"descr__contains": tag})
        if existing:
            return {"rule": existing[0], "tracker": existing[0].get("tracker"), "created": False}
        descr = f"{_MARKER.sub('', str(rule.get('descr') or ''))} {tag}".strip()
        created = await self._data("POST", "/firewall/rule", json={**rule, "descr": descr, "apply": True})
        return {"rule": created, "tracker": created.get("tracker"), "created": True}

    async def update_firewall_rule(
        self, tracker: str | int, changes: dict[str, Any], action_key: str
    ) -> dict[str, Any]:
        """The previous rule is returned as `snapshot` (JSON) — what the compensation restores. A
        replay that finds its own marker already there did the change and lost the answer: the
        previous rule is no longer known, and it says so (`snapshot` empty) instead of inventing it."""
        current = await self._by_tracker(tracker)
        if current is None:
            raise AdapterError(f"pfsense: no rule with tracker {tracker}")
        tag = marker(action_key)
        if tag in str(current.get("descr") or ""):
            return {"rule": current, "tracker": current.get("tracker"), "changed": False, "snapshot": ""}
        forbidden = sorted({"id", "tracker"} & set(changes))
        if forbidden:
            raise AdapterError(f"pfsense: {', '.join(forbidden)} cannot be changed")
        descr = f"{_MARKER.sub('', str(changes.get('descr', current.get('descr')) or ''))} {tag}".strip()
        snapshot = json.dumps({"rule": current, "placement": current.get("id")}, sort_keys=True)
        body = {**changes, "id": current["id"], "descr": descr, "apply": True}
        updated = await self._data("PATCH", "/firewall/rule", json=body)
        return {"rule": updated, "tracker": current.get("tracker"), "changed": True, "snapshot": snapshot}

    async def delete_firewall_rule(self, tracker: str | int) -> dict[str, Any]:
        current = await self._by_tracker(tracker)
        if current is None:
            return {"tracker": tracker, "deleted": False, "snapshot": ""}
        snapshot = json.dumps({"rule": current, "placement": current.get("id")}, sort_keys=True)
        await self._data("DELETE", "/firewall/rule", params={"id": current["id"], "apply": "true"})
        return {"tracker": current.get("tracker"), "deleted": True, "snapshot": snapshot}

    async def restore_firewall_rule(self, snapshot: str) -> dict[str, Any]:
        """The rule as the snapshot recorded it: updated in place if it still exists, recreated at
        its recorded position otherwise. No snapshot: nothing is known to restore — refused, named."""
        if not snapshot.strip():
            raise AdapterError(
                "pfsense: no snapshot — the previous rule was not recorded, nothing is restored"
            )
        try:
            recorded = json.loads(snapshot)
            rule = dict(recorded["rule"])
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise AdapterError(f"pfsense: unreadable snapshot ({error})") from error
        fields = {k: v for k, v in rule.items() if k not in {"id", "tracker"}}
        current = await self._by_tracker(rule.get("tracker", ""))
        if current is not None:
            restored = await self._data(
                "PATCH", "/firewall/rule", json={**fields, "id": current["id"], "apply": True}
            )
            return {"rule": restored, "tracker": current.get("tracker"), "restored": "updated"}
        body = {**fields, "apply": True}
        if recorded.get("placement") is not None:
            body["placement"] = recorded["placement"]
        recreated = await self._data("POST", "/firewall/rule", json=body)
        return {"rule": recreated, "tracker": recreated.get("tracker"), "restored": "recreated"}

    # ───────────────────────────── the registry's seam ─────────────────────────────

    async def execute(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        """A declared operation, by name: what the broker and the `connector.call` effect call."""
        if operation not in {o.name for o in self.operations}:
            raise AdapterError(f"pfsense has no operation {operation}")
        try:
            result: dict[str, Any] = await getattr(self, operation)(**params)
        except TypeError as error:
            raise AdapterError(f"pfsense {operation}: {error}") from error
        return result

    executer = execute  # lang: fr-ok alias:internal

    async def test(self) -> dict[str, Any]:
        await self._data("GET", "/status/gateways")
        return {"ok": True, "base_url": self.base_url, "tls_verified": self.tls_verified}

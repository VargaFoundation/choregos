# SPDX-License-Identifier: Apache-2.0
"""The fakes of the HR scenario (ADR 0005, S20-04): a device manager, a carrier, badge readers —
and the laptop supplier's agent.

Each one holds the operations of its family (`families.py`) as a real system would:
- every write is IDEMPOTENT, because a governed action is replayed (ADR 0035): enrolling the same
  device twice gives one enrolment, ordering the same reference twice gives one order;
- a write that CONTRADICTS the state is refused, by name: a device enrolled for someone else, a
  reference already taken by another shipment, an unknown badge someone thinks they are cutting;
- the instance's key is checked on every call, when the fake expects one.

One fake is reached in two ways: typed, by a `demo` connector (`Desk`), or served as an MCP server
(`mcp_server`), which an `mcp` connector reaches. The supplier's agent only exists under the
second: it is a third party, its tools are discovered.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import inspect
from dataclasses import InitVar, dataclass, field
from functools import partial
from typing import Any, ClassVar

from ..errors import AdapterError, UpstreamError
from ..families import ACCESS_CONTROL_OPERATIONS, MDM_OPERATIONS, SHIPPING_OPERATIONS
from ..registry import OperationSpec, schema_d_entree
from .mcp import FakeMcpServer


def _fingerprint(reference: str) -> str:
    """A number drawn from the reference, not from a counter: a replayed gesture returns the same."""
    return hashlib.sha256(reference.encode()).hexdigest()[:8].upper()


@dataclass
class BusinessFake:
    """What the fakes of the scenario share: declared operations, called by name, and the key they
    expect (empty: none)."""

    operations: ClassVar[tuple[OperationSpec, ...]] = ()
    #: `old name -> (new name, {old parameter: new parameter})`: answered, never announced.
    former_names: ClassVar[dict[str, tuple[str, dict[str, str]]]] = {}
    expected_key: str = field(default="", repr=False, kw_only=True)
    #: The former keyword of `expected_key`.
    cle_attendue: InitVar[str | None] = field(default=None, kw_only=True)  # lang: fr-ok alias:internal

    def __post_init__(self, cle_attendue: str | None) -> None:  # lang: fr-ok alias:internal
        if cle_attendue is not None:  # lang: fr-ok alias:internal
            self.expected_key = cle_attendue  # lang: fr-ok alias:internal

    def authenticate(self, key: str) -> None:
        if self.expected_key and not hmac.compare_digest(key.encode(), self.expected_key.encode()):
            raise UpstreamError(
                type(self).__name__,
                "clé refusée",  # lang: fr-ok (pinned by a test of apps/api)
                status_code=401,
            )

    def perform(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        if operation in self.former_names:
            operation, renamed = self.former_names[operation]
            params = {renamed.get(name, name): value for name, value in params.items()}
        if operation not in {o.name for o in self.operations}:
            raise AdapterError(f"{type(self).__name__} has no operation {operation}")
        method = getattr(self, operation)
        try:
            inspect.signature(method).bind(**params)
        except TypeError as error:
            raise AdapterError(f"{operation}: {error}") from error
        return dict(method(**params))

    def state(self) -> dict[str, Any]:
        """The simulated system's state, copied: what a replayed gesture must not change."""
        return copy.deepcopy({key: value for key, value in vars(self).items() if key != "expected_key"})

    etat = state  # lang: fr-ok alias:internal


@dataclass
class FakeMdm(BusinessFake):
    operations = MDM_OPERATIONS
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def postes(self) -> dict[str, dict[str, Any]]:  # lang: fr-ok alias:internal
        return self.devices

    def enroll_device(self, serial: str, upn: str) -> dict[str, Any]:
        device = self.devices.get(serial)
        if device is not None and device["state"] == "enrolled":
            if device["upn"] != upn:
                # The message is pinned by a test of apps/api (a former French spelling).
                raise AdapterError(f"le poste {serial} est inscrit pour un autre utilisateur")  # lang: fr-ok
            return {**device, "enrolled": False}
        self.devices[serial] = {"serial": serial, "upn": upn, "state": "enrolled"}
        return {**self.devices[serial], "enrolled": True}

    def wipe_device(self, serial: str) -> dict[str, Any]:
        device = self.devices.get(serial)
        if device is None:
            raise AdapterError(f"no device {serial} in the fleet")
        wiped = device["state"] != "wiped"
        # Once wiped, the device is released: it can be enrolled for a joiner.
        device.update(state="wiped", upn=None)
        return {**device, "wiped": wiped}

    def device_status(self, serial: str) -> dict[str, Any]:
        return dict(self.devices.get(serial) or {"serial": serial, "upn": None, "state": "unknown"})


@dataclass
class FakeCarrier(BusinessFake):
    operations = SHIPPING_OPERATIONS
    shipments: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def envois(self) -> dict[str, dict[str, Any]]:  # lang: fr-ok alias:internal
        return self.shipments

    def _ship(self, reference: str, address: str, direction: str, items: list[str]) -> dict[str, Any]:
        existing = self.shipments.get(reference)
        if existing is not None:
            if (existing["direction"], existing["address"]) != (direction, address):
                raise AdapterError(f"reference {reference} is already taken by another shipment")
            return {**existing, "created": False}
        outbound = direction == "outbound"
        self.shipments[reference] = {
            "reference": reference,
            "direction": direction,
            "address": address,
            "items": list(items),
            "tracking": f"{'TRK' if outbound else 'RET'}-{_fingerprint(reference)}",
            "state": "shipped" if outbound else "collection_booked",
        }
        return {**self.shipments[reference], "created": True}

    def create_shipment(self, reference: str, address: str, items: list[str] | None = None) -> dict[str, Any]:
        return self._ship(reference, address, "outbound", items or [])

    def create_return(self, reference: str, address: str) -> dict[str, Any]:
        return self._ship(reference, address, "return", [])

    def shipment_status(self, reference: str) -> dict[str, Any]:
        return dict(self.shipments.get(reference) or {"reference": reference, "state": "unknown"})

    def deliver(self, reference: str) -> None:
        """Staging: the parcel has arrived, or been collected."""
        shipment = self.shipments[reference]
        shipment["state"] = "delivered" if shipment["direction"] == "outbound" else "collected"


@dataclass
class FakeBadges(BusinessFake):
    operations = ACCESS_CONTROL_OPERATIONS
    badges: dict[str, dict[str, Any]] = field(default_factory=dict)

    def activate_badge(self, uid: str, holder: str) -> dict[str, Any]:
        badge = self.badges.get(uid)
        if badge is not None and badge["active"]:
            if badge["holder"] != holder:
                raise AdapterError(f"badge {uid} is active for another holder")
            return {**badge, "activated": False}
        self.badges[uid] = {"uid": uid, "holder": holder, "active": True}
        return {**self.badges[uid], "activated": True}

    def deactivate_badge(self, uid: str) -> dict[str, Any]:
        badge = self.badges.get(uid)
        if badge is None:
            # Not a success: a mistyped UID would leave the real badge opening the doors.
            raise AdapterError(f"no badge {uid}")
        cut = bool(badge["active"])
        badge["active"] = False
        return {**badge, "deactivated": cut}

    def badge_status(self, uid: str) -> dict[str, Any]:
        return dict(self.badges.get(uid) or {"uid": uid, "holder": None, "active": False})


#: The supplier agent's tools. It announces them itself, over MCP: the platform discovers them, and
#: they are born closed (ADR 0034).
SUPPLIER_TOOLS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "order_laptop",
        "write",
        "orders a laptop for a joiner; one order per reference",
        schema_d_entree("reference", "model", "upn"),
    ),
    OperationSpec(
        "order_status",
        "read",
        "where an order stands: its state, the laptop's serial number, the parcel's tracking",
        schema_d_entree("reference"),
    ),
)

#: The names the supplier's tools had before S39-11 (and the old parameter names), still answered
#: but no longer announced: a workflow published from the 1.x template keeps calling them. They are
#: tenant names, not ours to rename.
FORMER_SUPPLIER_TOOLS: dict[str, tuple[str, dict[str, str]]] = {
    "commander_poste": ("order_laptop", {"modele": "model"}),  # lang: fr-ok
    "suivi_commande": ("order_status", {}),  # lang: fr-ok
}


@dataclass
class FakeSupplier(BusinessFake):
    operations = SUPPLIER_TOOLS
    former_names = FORMER_SUPPLIER_TOOLS
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def commandes(self) -> dict[str, dict[str, Any]]:  # lang: fr-ok alias:internal
        return self.orders

    def order_laptop(self, reference: str, model: str, upn: str) -> dict[str, Any]:
        existing = self.orders.get(reference)
        if existing is not None:
            if (existing["model"], existing["upn"]) != (model, upn):
                raise AdapterError(f"order {reference} already exists, for another laptop")
            return {**existing, "created": False}
        self.orders[reference] = {
            "reference": reference,
            "model": model,
            "upn": upn,
            "serial": f"PC-{_fingerprint(reference)}",
            "state": "confirmed",
            "tracking": None,
        }
        return {**self.orders[reference], "created": True}

    def order_status(self, reference: str) -> dict[str, Any]:
        return dict(self.orders.get(reference) or {"reference": reference, "state": "unknown"})

    def ship(self, reference: str) -> None:
        """Staging: the supplier ships the laptop."""
        self.orders[reference].update(state="shipped", tracking=f"COLIS-{_fingerprint(reference)}")


#: The fake of each family, by connector kind.
FAKES_BY_FAMILY: dict[str, type[BusinessFake]] = {
    "mdm": FakeMdm,
    "shipping": FakeCarrier,
    "access_control": FakeBadges,
}


@dataclass
class Desk:
    """What a `demo` connector builds: the fake of its family, shared in the process, and the key
    of ITS instance — resolved by the platform, presented on every call, never returned."""

    fake: BusinessFake
    key: str = field(default="", repr=False)

    async def execute(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        self.fake.authenticate(self.key)
        return self.fake.perform(operation, params)

    executer = execute  # lang: fr-ok alias:internal

    async def test(self) -> dict[str, Any]:
        self.fake.authenticate(self.key)
        return {"ok": True, "fake": type(self.fake).__name__}


@dataclass
class RemoteDesk:
    """A `demo` connector that carries a `url`: the fake of its family served by ONE process
    (`fakes/server.py`), reached over MCP — the API and the orchestrator then see the same state."""

    client: Any

    async def execute(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        result = await self.client.call_tool(operation, params)
        if result.get("isError"):
            content = result.get("content") or [{}]
            raise AdapterError(str(content[0].get("text") or f"{operation} refused"))
        structure = result.get("structuredContent")
        return dict(structure) if isinstance(structure, dict) else {}

    executer = execute  # lang: fr-ok alias:internal

    async def test(self) -> dict[str, Any]:
        result: dict[str, Any] = await self.client.test()
        return result


def mcp_server(
    fake: BusinessFake,
    *,
    token: str = "",
    jeton: str | None = None,  # lang: fr-ok alias:internal
) -> FakeMcpServer:
    """The same fake, served as an MCP server: each operation becomes a tool (a read carries
    `readOnlyHint`) and each call plays the same method, on the same state."""
    if jeton is not None:  # lang: fr-ok alias:internal
        token = jeton  # lang: fr-ok alias:internal
    return FakeMcpServer(
        outils=[
            {
                "name": operation.name,
                "description": operation.description,
                "inputSchema": operation.input_schema or {"type": "object"},
                **({"annotations": {"readOnlyHint": True}} if operation.access == "read" else {}),
            }
            for operation in fake.operations
        ],
        jeton=token,
        gestes={
            name: partial(fake.perform, name)
            for name in [*(operation.name for operation in fake.operations), *fake.former_names]
        },
    )


# Former names that other code imports (ADR 0098 §3 (b)); `fakes/rh.py` keeps the former path.
FauxMetier = BusinessFake  # lang: fr-ok alias:internal
FakeTransporteur = FakeCarrier  # lang: fr-ok alias:internal
FakeFournisseur = FakeSupplier  # lang: fr-ok alias:internal
FAUX_PAR_FAMILLE = FAKES_BY_FAMILY  # lang: fr-ok alias:internal
OUTILS_DU_FOURNISSEUR = SUPPLIER_TOOLS  # lang: fr-ok alias:internal
ANCIENS_OUTILS_DU_FOURNISSEUR = FORMER_SUPPLIER_TOOLS  # lang: fr-ok alias:internal
Guichet = Desk  # lang: fr-ok alias:internal
GuichetDistant = RemoteDesk  # lang: fr-ok alias:internal
serveur_mcp = mcp_server  # lang: fr-ok alias:internal

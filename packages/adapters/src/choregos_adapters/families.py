# SPDX-License-Identifier: Apache-2.0
"""The business connector families (ADR 0034, S20-04): what a device manager, a carrier and an
access control system promise — their operations, read or write, and the schema of what they expect.

A family is not an implementation. The `demo` type holds it today, in memory; a real type (Intune,
a carrier, a reader maker) will take these operations over as they are, and the conformance suite
(`tests/conformance/connecteurs/`) will judge them the same way: schemas that the implementation
follows, idempotent writes — a governed action is replayed —, no secret in the log.
"""

from __future__ import annotations

from .marketdata.base import DATE_PATTERN, SYMBOL_PATTERN
from .registry import OperationSpec, schema_d_entree

#: `mdm`: the fleet. A device is enrolled for its user and wiped when it comes back.
MDM_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "enroll_device",
        "write",
        "enrols a device for its user (already enrolled for them: done)",
        schema_d_entree("serial", "upn"),
    ),
    OperationSpec(
        "wipe_device",
        "write",
        "wipes a returned device and releases it (already wiped: done)",
        schema_d_entree("serial"),
    ),
    OperationSpec(
        "device_status", "read", "reads a device: its user and its state", schema_d_entree("serial")
    ),
)

#: `shipping`: a shipment or a collection, one per reference — the reference is the idempotency key.
SHIPPING_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "create_shipment",
        "write",
        "ships a parcel to an address (one shipment per reference)",
        schema_d_entree("reference", "address", items={"type": "array", "items": {"type": "string"}}),
    ),
    OperationSpec(
        "create_return",
        "write",
        "books the collection of a parcel at an address (one return per reference)",
        schema_d_entree("reference", "address"),
    ),
    OperationSpec(
        "shipment_status",
        "read",
        "tracks a shipment or a return: its state and tracking number",
        schema_d_entree("reference"),
    ),
)

#: `access_control`: a badge, active for its holder or cut.
ACCESS_CONTROL_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "activate_badge",
        "write",
        "activates a badge for its holder (already active for them: done)",
        schema_d_entree("uid", "holder"),
    ),
    OperationSpec(
        "deactivate_badge",
        "write",
        "deactivates a badge (already inactive: done)",
        schema_d_entree("uid"),
    ),
    OperationSpec(
        "badge_status", "read", "reads a badge: its holder and whether it opens", schema_d_entree("uid")
    ),
)

#: `marketdata`: daily bars and the description of a symbol, from free sources. Read-only — no write,
#: so no approval: the platform is the go-between (the sandbox's egress stays closed, keys stay out of
#: the pod, every call is counted). See `marketdata/`.
OPERATIONS_MARKETDATA: tuple[OperationSpec, ...] = (
    OperationSpec(
        "get_bars",
        "read",
        "reads the daily bars (open, high, low, close, adjusted close, volume) of a stock or ETF "
        "between two dates, sorted, one per date",
        schema_d_entree(
            "symbol",
            "start",
            "end",
            symbol={"type": "string", "pattern": SYMBOL_PATTERN, "description": "e.g. AAPL, MC.PA, ^GSPC"},
            start={"type": "string", "pattern": DATE_PATTERN, "description": "YYYY-MM-DD"},
            end={"type": "string", "pattern": DATE_PATTERN, "description": "YYYY-MM-DD, not before start"},
        ),
    ),
    OperationSpec(
        "get_instrument",
        "read",
        "reads what a symbol is: its name, whether it is a stock or an ETF, its currency and exchange",
        schema_d_entree(
            "symbol", symbol={"type": "string", "pattern": SYMBOL_PATTERN, "description": "e.g. AAPL"}
        ),
    ),
)

#: The families and their operations, by connector kind.
FAMILIES: dict[str, tuple[OperationSpec, ...]] = {
    "mdm": MDM_OPERATIONS,
    "shipping": SHIPPING_OPERATIONS,
    "access_control": ACCESS_CONTROL_OPERATIONS,
}


# Former names that other code imports (ADR 0098 §3 (b)); `familles.py` keeps the former path.
OPERATIONS_MDM = MDM_OPERATIONS  # lang: fr-ok alias:internal
OPERATIONS_EXPEDITION = SHIPPING_OPERATIONS  # lang: fr-ok alias:internal
OPERATIONS_CONTROLE_D_ACCES = ACCESS_CONTROL_OPERATIONS  # lang: fr-ok alias:internal
FAMILLES = FAMILIES  # lang: fr-ok alias:internal

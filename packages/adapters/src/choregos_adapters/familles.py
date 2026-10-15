# SPDX-License-Identifier: Apache-2.0
"""Les familles de connecteurs métier (ADR 0034, S20-04) : ce que promettent un gestionnaire de
parc, un transporteur, un contrôle d'accès — leurs opérations, lecture ou écriture, et le schéma de
ce qu'elles attendent.

Une famille n'est pas une implémentation. Le type `demo` la tient aujourd'hui, en mémoire ; un vrai
type (Intune, un transporteur, un fabricant de lecteurs) reprendra ces opérations telles quelles, et
la suite de conformité (`tests/conformance/connecteurs/`) les jugera de la même façon : des schémas
que l'implémentation suit, des écritures idempotentes — une action gouvernée se rejoue —, aucun
secret au journal.
"""

from __future__ import annotations

from .marketdata.base import DATE_PATTERN, SYMBOL_PATTERN
from .registry import OperationSpec, schema_d_entree

#: `mdm` : le parc. Un poste s'inscrit au nom de son utilisateur et s'efface à son retour.
OPERATIONS_MDM: tuple[OperationSpec, ...] = (
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

#: `shipping` : un envoi ou une reprise, un par référence — la référence est la clé d'idempotence.
OPERATIONS_EXPEDITION: tuple[OperationSpec, ...] = (
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

#: `access_control` : un badge, actif pour son porteur ou coupé.
OPERATIONS_CONTROLE_D_ACCES: tuple[OperationSpec, ...] = (
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

#: Les familles et leurs opérations, par sorte de connecteur.
FAMILLES: dict[str, tuple[OperationSpec, ...]] = {
    "mdm": OPERATIONS_MDM,
    "shipping": OPERATIONS_EXPEDITION,
    "access_control": OPERATIONS_CONTROLE_D_ACCES,
}

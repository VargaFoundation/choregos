# SPDX-License-Identifier: Apache-2.0
"""The values of the contracts' open lists that are software delivery's (ADR 0181, round C2): the inbound
event types of a source control, a pipeline and a deployment (`scm.*`, `ci.*`, `cd.*`), the finding types
`bug`, `perf`, `tech-debt` and `flaky-test`, the memory kinds `flaky_test` and `hotspot`, and the
connector kinds `scm`, `ci` and `cd`.

The strings do not change: they are stored in `received_events`, the findings and the memories. The core's
lists keep them for the window of round C2 (`OpenList.kept`, on the window list with the release that
removes them); from then on only this declaration makes them valid.
"""

from __future__ import annotations

from tranibor_contracts import CONNECTOR_KINDS, FINDING_TYPES, INBOUND_EVENT_TYPES, MEMORY_KINDS
from tranibor_contracts.window import (
    DELIVERY_CONNECTOR_KINDS,
    DELIVERY_EVENT_TYPES,
    DELIVERY_FINDING_TYPES,
    DELIVERY_MEMORY_KINDS,
)

from .names import NAME

#: Each open list, and the values the plugin declares in it.
DECLARED = (
    (INBOUND_EVENT_TYPES, DELIVERY_EVENT_TYPES),
    (FINDING_TYPES, DELIVERY_FINDING_TYPES),
    (MEMORY_KINDS, DELIVERY_MEMORY_KINDS),
    (CONNECTOR_KINDS, DELIVERY_CONNECTOR_KINDS),
)


def declare_values() -> None:
    """Declare the plugin's values in the contracts' open lists. Idempotent."""
    for open_list, values in DECLARED:
        for value in sorted(values):
            open_list.declare(str(value), plugin=NAME)

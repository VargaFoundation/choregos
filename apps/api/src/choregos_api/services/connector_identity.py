# SPDX-License-Identifier: Apache-2.0
"""Which identity modes a connector can carry (ADR 0135): the one place that answers it.

`service` is the connector's own credential and always works. `user_exchange` and `user_consent` call the
source as the person: a connector supports them only when its configuration declares it, as
`identity_modes: [user_exchange, ...]`. Binding an assistant's need (S63-02) asks this function; the
declaration itself is built by S63-07, which adopts this function rather than writing a second check.
"""

from __future__ import annotations

from ..db.models import OrgConnector

SERVICE = "service"
IDENTITY_MODES = (SERVICE, "user_exchange", "user_consent")


def supports_identity(connector: OrgConnector, mode: str) -> bool:
    """Whether `connector` can be called under `mode`."""
    if mode == SERVICE:
        return True
    declared = (connector.config or {}).get("identity_modes")
    return isinstance(declared, list) and mode in declared

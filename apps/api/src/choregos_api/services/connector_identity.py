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


#: A connector reached through an outpost (ADR 0149 §2): the call reaches the outpost's server under the
#: credential the outpost holds for the alias, and under no other.
OUTPOST_TYPE = "mcp-outpost"


def supports_identity(connector: OrgConnector, mode: str) -> bool:
    """Whether `connector` can be called under `mode`.

    A connector at an outpost supports `service` alone, whatever its row says: a person's token must
    not rest in a row of the platform nor cross a process that is not its audience, so none travels
    to an outpost (ADR 0149 §2). Its declared schema is closed and has no `identity_modes`, so a need
    that must be called as the person is refused when it is bound; this is the second lock."""
    if mode == SERVICE:
        return True
    if connector.type == OUTPOST_TYPE:
        return False
    declared = (connector.config or {}).get("identity_modes")
    return isinstance(declared, list) and mode in declared

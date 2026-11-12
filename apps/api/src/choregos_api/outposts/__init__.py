# SPDX-License-Identifier: Apache-2.0
"""The outposts' door: the third door of the API, the one an outpost's daemon dials (ADR 0148).

`/mcp` serves a person's agent and `/a2a` another agent; this one serves the place where an
organisation's tools live and the platform cannot reach (ADR 0147). An outpost opens every connection
itself, holds a key pair and no token, and is handed assignments: the calls its organisation's
connectors route to it (ADR 0149). It is never handed MCP.

- `auth`: a request is authenticated by a signature that binds it; every problem is the contract's.
- `door`: the four verbs, and the long poll that holds no connection while it waits.
- `rendezvous`: a call as a row with a short life, where the door and whoever routes a call meet.

Mounted only when `CHOREGOS_OUTPOSTS` is on (`main.py`); its contract is
`packages/contracts/outpost-door.yaml`.
"""

from __future__ import annotations

from .auth import check_the_door
from .door import router

__all__ = ["check_the_door", "router"]

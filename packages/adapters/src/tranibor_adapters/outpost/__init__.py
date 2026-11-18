# SPDX-License-Identifier: Apache-2.0
"""A tool server that lives at an outpost (ADR 0149): the port of the rendezvous, the tool client
that calls through it, and the typed refusals of that route.

The platform never connects to an outpost (ADR 0147): a connector of type `mcp-outpost` names an
outpost and an endpoint alias, and its calls are placed where an instance of that outpost claims
them. The rendezvous is a port (`port.Rendezvous`); the platform implements it over its database and
registers the factory that builds the client with it, as it does for the lexical memory. Nothing
here imports the platform, and nothing here extends the MCP client.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .client import DEFAULT_TIMEOUT_S, OutpostToolClient
from .port import Call, NotAdmitted, Outcome, Rendezvous
from .refusals import (
    OutpostInDoubt,
    OutpostNotAdmitted,
    OutpostOffline,
    OutpostPaused,
    OutpostRefusal,
    OutpostRefused,
    OutpostTimeout,
)


def client_of(config: Mapping[str, Any], rendezvous: Rendezvous) -> OutpostToolClient:
    """The tool client of a connector's configuration, over the rendezvous its platform supplies.
    `asserted` and `write_key` are the platform's, per call; neither is ever stored."""
    return OutpostToolClient(
        rendezvous,
        endpoint=str(config["endpoint"]),
        timeout_s=float(config.get("timeout_s") or DEFAULT_TIMEOUT_S),
        asserted=config.get("asserted"),
        write_key=config.get("write_key"),
    )


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "Call",
    "NotAdmitted",
    "Outcome",
    "OutpostInDoubt",
    "OutpostNotAdmitted",
    "OutpostOffline",
    "OutpostPaused",
    "OutpostRefusal",
    "OutpostRefused",
    "OutpostTimeout",
    "OutpostToolClient",
    "Rendezvous",
    "client_of",
]

# SPDX-License-Identifier: Apache-2.0
"""`choregos-outpost`: the daemon an outpost's operator installs (ADRs 0147, 0148, 0149).

An outpost is a place a person enrols, of one organisation, that only dials out. This program holds
a key pair it generated, polls the platform's door for the calls routed to it, makes them against
MCP servers of its own network under a policy file the platform cannot write, and answers.

What it does not do, by construction (ADR 0147 §9): it opens no listening socket, it calls nothing
but the door and the endpoints its policy file names, it sends no telemetry, it never updates
itself, and no message of the door makes it download or run anything.
"""

from __future__ import annotations

from importlib import metadata as _metadata

from choregos_contracts.outpost import OUTPOST_PROTOCOL

#: The daemon's own release, announced to the door as a fact to show. Read from the installed
#: distribution, so that it is the version that was built and not a constant somebody forgot.
try:
    __version__ = _metadata.version("choregos-outpost")
except _metadata.PackageNotFoundError:  # a source tree that was not installed
    __version__ = "0.0.0+unknown"

#: The protocol this daemon speaks. A door whose minimum is above it is one this daemon stops
#: polling, and says so: nobody upgrades it for its operator.
PROTOCOL = OUTPOST_PROTOCOL

__all__ = ["PROTOCOL", "__version__"]

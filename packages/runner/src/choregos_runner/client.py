# SPDX-License-Identifier: Apache-2.0
"""The internal API client lives in `choregos_tools_mcp.client`; this module re-exports it.

The runner imports the tools server (to start it in its pod) and the tools server
used to import the runner's client: a package cycle, resolved only because uv works in a
workspace; outside it, neither wheel installed (state of play of 2026-09-24). The client
moved to the one that needs it most; the runner depends on it, not the other way round.
"""

from __future__ import annotations

from choregos_tools_mcp.client import InternalApiError, InternalClient

__all__ = ["InternalApiError", "InternalClient"]

# SPDX-License-Identifier: Apache-2.0
"""The MCP door of the external clients: Claude Code, Claude Desktop, Cursor, VS Code... (ADR 0030).

The `choregos-tools` side-car serves a run's agent, from inside the pod. This door serves a
PERSON's agent, from outside: it acts with that person's rights, never more, and never decides — a
decision needs a re-authenticated session, in the console.
"""

from __future__ import annotations

from .transport import router

__all__ = ["router"]

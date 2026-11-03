# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal — former MCP door module (ADR 0098), removed by S45-48
"""The former name of `choregos_api.mcp.caller`: the same module object under its English name."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .caller import (
    MCP_SCOPES,
    SCOPES_A2A,
    Caller,
    Refusal,
    act_as_agent,
    caller_of_token,
    identify,
)

__all__ = ["SCOPES_A2A", "caller_of_token"]

Appelant = Caller  # lang: fr-ok alias:internal
Refus = Refusal  # lang: fr-ok alias:internal
PORTEES_MCP = MCP_SCOPES  # lang: fr-ok alias:internal
identifier = identify  # lang: fr-ok alias:internal
incarner_l_agent = act_as_agent  # lang: fr-ok alias:internal

replace_module(__name__, sys.modules["choregos_api.mcp.caller"])

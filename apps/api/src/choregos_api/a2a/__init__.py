# SPDX-License-Identifier: Apache-2.0
"""The A2A door: governed workflows served as A2A skills to outside agents (ADR 0078).

The MCP door offers tools to a person's agent; this door offers an organisation's opted-in
workflows as skills, and a work item as an A2A task. It shares the MCP door's tokens, OAuth,
agents, rate limit and daily write budget, and like it never decides: a decision is taken by a
re-authenticated person in the console.
"""

from __future__ import annotations

from .door import router

__all__ = ["router"]

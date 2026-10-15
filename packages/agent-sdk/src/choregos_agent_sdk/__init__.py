# SPDX-License-Identifier: Apache-2.0
"""Choregos agent SDK: write an agent in Python, run it under the platform's guarantees.

An agent is one async function. The SDK speaks ACP to the runner, sends every tool call and every
write through the runner's permission path, counts turns against the step's limit, and writes the
result in the contract's shape.
"""

from __future__ import annotations

from ._version import __version__
from .agent import Agent, agent
from .context import Context
from .errors import AgentSdkError, LlmError, MaxTurnsExceeded, PermissionDenied, ToolError
from .llm import Llm, LlmReply
from .result import Result
from .tools import ToolResult, Tools, ToolSpec

__all__ = [
    "Agent",
    "AgentSdkError",
    "Context",
    "Llm",
    "LlmError",
    "LlmReply",
    "MaxTurnsExceeded",
    "PermissionDenied",
    "Result",
    "ToolError",
    "ToolResult",
    "ToolSpec",
    "Tools",
    "__version__",
    "agent",
]

"""`choregos-agent-sdk` is a run-contract adapter, not an agent framework: its surface is frozen.

A planner, a memory, a graph primitive added here turns this red. It needs an amendment of ADR 0063
("No Choregos framework"), not a quiet export.
"""

from __future__ import annotations

import choregos_agent_sdk
from choregos_agent_sdk import integrations

FROZEN = {
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
}


def test_the_public_names_are_the_frozen_ones() -> None:
    assert set(choregos_agent_sdk.__all__) == FROZEN


def test_the_integrations_are_the_three_bridges() -> None:
    assert set(integrations.__all__) == {"langchain", "openai_agents", "pydantic_ai"}

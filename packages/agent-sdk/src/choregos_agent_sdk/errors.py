# SPDX-License-Identifier: Apache-2.0
"""What an agent written with the SDK can be refused, and how it says so."""

from __future__ import annotations


class AgentSdkError(Exception):
    """Base of the errors the SDK raises."""


class PermissionDenied(AgentSdkError):  # noqa: N818 - a refusal, said as one
    """The runner's guard refused an action: a tool call, a write. The action did not happen.

    Let it propagate and the step ends `failed` with `reason=permission_denied`; or catch it and
    carry on without that action. Never retry the same call in a loop: the runner stops that.
    """

    def __init__(self, action: str, reason: str = "") -> None:
        self.action = action
        self.reason = reason
        super().__init__(f"refused: {action}" + (f" ({reason})" if reason else ""))


class MaxTurnsExceeded(AgentSdkError):  # noqa: N818 - the step's own limit
    """The agent reached the step's `max_turns` (model calls and tool calls): it stops there."""


class ToolError(AgentSdkError):
    """A tool the agent called answered with an error, or could not be reached."""


class LlmError(AgentSdkError):
    """The model could not be called: no gateway in this run, or the call failed."""

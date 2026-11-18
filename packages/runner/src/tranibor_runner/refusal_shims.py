# SPDX-License-Identifier: Apache-2.0
"""What a refusal shim refused, journaled by the runner (#1346, ADR 0115).

The runner image puts `choregos-refuse` (`docker/shims/refuse.sh`) in the place of the tools an agent
has no reason to call from its workspace (`docker/runner.Dockerfile`). The shim says so on standard
error and exits 127. The agent's shell tool hands that output back to the agent, which reports it in
the call's `session/update` — which the runner journals. The runner reads the shim's line there and
journals a `shim.refused` event right after it: `{"tool", "tool_call_id"}`.

The shim used to post the event itself with the run token. The agent no longer holds one (ADR 0115,
S57-02), and nothing the agent runs may: reading the line where the runner already journals it needs
no credential, and gives the agent no new way in.

The line can be forged — the agent can print it — and that is harmless: the event names one of the
shimmed tools or nothing (another name is ignored), it says only that a call of the agent printed the
refusal, and no decision reads it. An agent that forges it reports itself. A refusal the agent's shell
does not hand back (`kubectl ... 2>/dev/null`) leaves no event; the call stays in the transcript.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

#: The journal's event type.
EVENT = "shim.refused"
#: The tools the runner image replaces with the shim (`docker/runner.Dockerfile`); a test holds the
#: two lists equal.
SHIMMED = frozenset({"aws", "az", "docker", "gcloud", "helm", "kubectl", "podman", "terraform"})
#: The shim's first line on standard error: "choregos: `<tool>` is refused in an agent workspace."
LINE = re.compile(r"choregos: `([A-Za-z0-9._-]{1,64})` is refused in an agent workspace\.")
#: The updates that carry a tool call and its output (ACP).
TOOL_UPDATES = frozenset({"tool_call", "tool_call_update"})


def _texts(value: Any) -> Iterator[str]:
    """Every string a JSON value holds, its keys aside: a backend reports a call's output in its
    `content`, its `rawOutput`, or both."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)


class ShimRefusals:
    """The refusals a session's tool calls printed: each one once per call and tool, however many
    updates repeat the call's output."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def of(self, params: dict[str, Any]) -> list[dict[str, str]]:
        """The `shim.refused` payloads a `session/update` adds, in the order of its text."""
        update = params.get("update")
        if not isinstance(update, dict) or update.get("sessionUpdate") not in TOOL_UPDATES:
            return []
        call = str(update.get("toolCallId") or "")
        found: list[dict[str, str]] = []
        for text in _texts(update):
            for match in LINE.finditer(text):
                tool = match.group(1)
                if tool in SHIMMED and (call, tool) not in self._seen:
                    self._seen.add((call, tool))
                    found.append({"tool": tool, "tool_call_id": call})
        return found

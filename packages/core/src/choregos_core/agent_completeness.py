# SPDX-License-Identifier: Apache-2.0
"""Is an agent version complete? (ADR 0127 §7, S60-04)

An agent is a closed set of five components. A version is **complete** when it inherits none of them from
its project or the deployment: `model` set, `tools.catalogue`, `tools.extensions` and `tools.web` present
(possibly empty: a list that is present closes its source), `memories` present (possibly empty).
Instructions and knowledge are never inherited, so they need no rule.

A remote (`a2a`) agent is complete by construction: it may declare none of them. A `voice` agent needs only
its model, which its runtime already requires: a session reaches its MCP servers and nothing else.

Pure, over the agent's `spec` as a dictionary (a catalogue entry's `document["spec"]`, a template's agent
file, a stored version): the catalogue test uses it today, the API's publication gate (S60-05) will.
An external agent (a client of the MCP door, ADR 0033 §7) is not concerned: the caller does not ask.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The components a complete version declares, in the order `missing_components` reports them.
COMPONENTS = ("model", "tools.catalogue", "tools.extensions", "tools.web", "memories")


def missing_components(spec: Mapping[str, Any]) -> list[str]:
    """What a version leaves to its project or the deployment; empty when it is complete."""
    runtime = spec.get("runtime")
    kind = runtime.get("kind") if isinstance(runtime, Mapping) else None
    if kind == "a2a":
        return []
    missing: list[str] = []
    model = spec.get("model")
    if not (isinstance(model, str) and model.strip()):
        missing.append("model")
    if kind == "voice":
        return missing
    tools = spec.get("tools")
    tools = tools if isinstance(tools, Mapping) else {}
    missing += [
        f"tools.{source}" for source in ("catalogue", "extensions", "web") if tools.get(source) is None
    ]
    if spec.get("memories") is None:
        missing.append("memories")
    return missing


def is_complete(spec: Mapping[str, Any]) -> bool:
    return not missing_components(spec)

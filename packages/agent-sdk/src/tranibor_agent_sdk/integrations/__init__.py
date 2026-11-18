# SPDX-License-Identifier: Apache-2.0
"""Bridges from the run's tools to a framework's own tool objects.

Do not hand the run's MCP URLs to a framework's native MCP client: it calls the servers directly,
which skips the runner's permission request, the journal and the turn count. A bridge builds the
framework's tools from the run's `ToolSpec`s and makes each call through `Tools.call`, which keeps
all three. No framework is a dependency of the SDK: each bridge imports its own lazily.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


def need(module: str, package: str) -> Any:
    """Imports `module`, or says which package to install."""
    try:
        return import_module(module)
    except ImportError as exc:
        raise ImportError(f"install {package} to use this bridge (`pip install {package}`)") from exc


__all__ = ["langchain", "openai_agents", "pydantic_ai"]

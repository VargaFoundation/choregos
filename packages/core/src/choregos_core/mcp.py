# SPDX-License-Identifier: Apache-2.0
"""The envelope of the MCP protocol, written by hand and shared (ADR 0017, ADR 0030).

Two servers speak it: the `choregos-tools` side-car, which serves its tools to the agent of a run,
and the `/mcp` door of the API, which serves a human's clients (Claude Code, Cursor, VS Code...).
Neither embeds the `mcp` SDK: seven short tools justify neither sessions nor one more transport
to audit, and ADR 0011 found critical flaws in `fastmcp`. This module does only what the protocol
requires of a stateless server, and says so.

What it does NOT do: JSON-RPC batches (removed from the protocol in 2025-06-18), SSE streams,
sessions, notifications from the server to the client.
"""

from __future__ import annotations

import json
from typing import Any, Final

#: The versions understood, the most recent first. A client that asks for another one gets the
#: most recent: the protocol lets it choose to disconnect.
VERSIONS: Final = ("2025-11-25", "2025-06-18", "2025-03-26")

#: How much of one tool result an agent reads, in bytes of JSON, its data counted once (#755, #1374):
#: the default of the API's `CHOREGOS_BROKER_RESULT_MAX_BYTES`, and the bound of what the run's tool
#: server shows the model of a relayed result, so that a result the API passes whole reaches the
#: model whole, as it reaches a script through the tool bridge (ADR 0122 §8).
RESULT_MAX_BYTES: Final = 65_536

#: JSON-RPC 2.0 codes used by MCP.
PARSE_ERROR: Final = -32700
INVALID_REQUEST: Final = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602
INTERNAL_ERROR: Final = -32603


def negotiate(requested: object) -> str:
    """The version the server answers to `initialize`."""
    return requested if isinstance(requested, str) and requested in VERSIONS else VERSIONS[0]


def acceptable_version(header: str | None) -> bool:
    """The `MCP-Protocol-Version` header of a request: absent, it stands for an old version."""
    return header is None or header in VERSIONS


def ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": body}


def text_result(
    text: str,
    *,
    is_error: bool = False,
    structured: Any = None,
) -> dict[str, Any]:
    """The result of a tool: text for the model, the structure for the client that reads it."""
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def serializes(block: Any, structured: Any) -> bool:
    """Whether a content block is `structured` serialized: a text block whose JSON is the same value,
    as MCP asks a server that returns structured content to write, and as the broker wraps the answer
    of a connector that is not MCP. A text that is not JSON, or another value, says something else."""
    if structured is None or not isinstance(block, dict) or block.get("type") != "text":
        return False
    text = block.get("text")
    if not isinstance(text, str):
        return False
    try:
        return bool(json.loads(text) == structured)
    except (ValueError, RecursionError):
        return False


def data_once(result: dict[str, Any]) -> dict[str, Any]:
    """An MCP result with its data once (#1374): the text blocks that serialize its
    `structuredContent` left out, since the structure says what they say. A text that says something
    else stays: `web_fetch`'s page beside what was fetched, a server's own summary. What the platform
    bounds, and what the run's tool server shows the model, are measured on this."""
    blocks = result.get("content")
    if result.get("structuredContent") is None or not isinstance(blocks, list):
        return result
    kept = [block for block in blocks if not serializes(block, result["structuredContent"])]
    return result if len(kept) == len(blocks) else {**result, "content": kept}


def is_notification(message: dict[str, Any]) -> bool:
    """A notification has no `id`: it expects no answer."""
    return "id" not in message


def message_defect(message: object) -> str | None:
    """Why this message is not an acceptable JSON-RPC 2.0 request, or `None`."""
    if isinstance(message, list):
        return "JSON-RPC batches are not supported (MCP 2025-06-18)"
    if not isinstance(message, dict):
        return "a JSON-RPC message is an object"
    if message.get("jsonrpc") != "2.0":
        return '`jsonrpc` must be "2.0"'
    if not isinstance(message.get("method"), str):
        return "`method` is missing"
    return None

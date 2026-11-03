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

from typing import Any, Final

#: The versions understood, the most recent first. A client that asks for another one gets the
#: most recent: the protocol lets it choose to disconnect.
VERSIONS: Final = ("2025-11-25", "2025-06-18", "2025-03-26")

#: JSON-RPC 2.0 codes used by MCP.
PARSE_ERROR: Final = -32700
INVALID_REQUEST: Final = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602
INTERNAL_ERROR: Final = -32603

ERREUR_DE_SYNTAXE: Final = PARSE_ERROR  # lang: fr-ok alias:internal
REQUETE_INVALIDE: Final = INVALID_REQUEST  # lang: fr-ok alias:internal
METHODE_INCONNUE: Final = METHOD_NOT_FOUND  # lang: fr-ok alias:internal
PARAMETRES_INVALIDES: Final = INVALID_PARAMS  # lang: fr-ok alias:internal
ERREUR_INTERNE: Final = INTERNAL_ERROR  # lang: fr-ok alias:internal


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
    en_erreur: bool | None = None,  # lang: fr-ok alias:internal
    structure: Any = None,  # lang: fr-ok alias:internal
) -> dict[str, Any]:
    """The result of a tool: text for the model, the structure for the client that reads it."""
    if en_erreur is not None:  # lang: fr-ok alias:internal
        is_error = en_erreur  # lang: fr-ok alias:internal
    if structure is not None:  # lang: fr-ok alias:internal
        structured = structure  # lang: fr-ok alias:internal
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if structured is not None:
        result["structuredContent"] = structured
    return result


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


negocier = negotiate  # lang: fr-ok alias:internal
version_acceptable = acceptable_version  # lang: fr-ok alias:internal
erreur = error  # lang: fr-ok alias:internal
resultat_texte = text_result  # lang: fr-ok alias:internal
est_notification = is_notification  # lang: fr-ok alias:internal
defaut_du_message = message_defect  # lang: fr-ok alias:internal

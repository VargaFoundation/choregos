"""Protocole ACP : JSON-RPC 2.0 sur stdio (docs/plan/02 §2.2).

Le runner est le **client** : il lance l'agent, l'initialise, ouvre une session,
lui envoie le prompt, et arbitre chaque demande de permission.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1
ACP_VERSION = "0.11"

# Méthodes émises par le client (le runner) vers l'agent.
INITIALIZE = "initialize"
AUTHENTICATE = "authenticate"
SESSION_NEW = "session/new"
SESSION_LOAD = "session/load"
SESSION_PROMPT = "session/prompt"
SESSION_CANCEL = "session/cancel"

# Méthodes émises par l'agent vers le client.
SESSION_UPDATE = "session/update"
SESSION_REQUEST_PERMISSION = "session/request_permission"
FS_READ_TEXT_FILE = "fs/read_text_file"
FS_WRITE_TEXT_FILE = "fs/write_text_file"

STOP_REASONS = frozenset({"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"})


@dataclass(slots=True)
class Request:
    id: int
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> bytes:
        return _encode({"jsonrpc": "2.0", "id": self.id, "method": self.method, "params": self.params})


@dataclass(slots=True)
class Notification:
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> bytes:
        return _encode({"jsonrpc": "2.0", "method": self.method, "params": self.params})


@dataclass(slots=True)
class Response:
    id: int
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None

    def encode(self) -> bytes:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.error is not None:
            payload["error"] = self.error
        else:
            payload["result"] = self.result or {}
        return _encode(payload)


def _encode(payload: dict[str, Any]) -> bytes:
    """Un message par ligne (JSON Lines), encodage UTF-8."""
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes | str) -> Request | Response | Notification | None:
    """Décode une ligne JSON-RPC. Une ligne vide ou du bruit rend `None`."""
    text = line.decode("utf-8") if isinstance(line, bytes) else line
    text = text.strip()
    if not text or not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if "method" in payload and "id" in payload:
        return Request(
            id=int(payload["id"]), method=str(payload["method"]), params=payload.get("params") or {}
        )
    if "method" in payload:
        return Notification(method=str(payload["method"]), params=payload.get("params") or {})
    if "id" in payload:
        return Response(id=int(payload["id"]), result=payload.get("result"), error=payload.get("error"))
    return None


def error_response(request_id: int, code: int, message: str) -> Response:
    return Response(id=request_id, error={"code": code, "message": message})


# Codes d'erreur JSON-RPC utilisés par le runner.
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
PERMISSION_DENIED = -32003

#!/usr/bin/env python3
"""Agent ACP factice : joue le protocole complet, scripté par une variable d'environnement.

Il sert la suite de conformité et les tests du runner : il répond à `initialize`,
ouvre une session, demande des permissions, écrit des fichiers, et produit un
`.choregos/result.json`. Son scénario est donné par `FAKE_AGENT_SCRIPT` (JSON).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1


def send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return {}
    try:
        return dict(json.loads(line))
    except json.JSONDecodeError:
        return {}


def request(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Envoie une requête au client (runner) et attend sa réponse."""
    send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    while True:
        message = read()
        if message is None:
            return None
        if message.get("id") == request_id and ("result" in message or "error" in message):
            return message


def main() -> int:
    script: dict[str, Any] = json.loads(os.environ.get("FAKE_AGENT_SCRIPT", "{}"))
    workspace = Path(os.environ.get("CHOREGOS_WORKSPACE_PATH", ".")).resolve()
    outgoing_id = 1000
    prompts_seen = 0

    while True:
        message = read()
        if message is None:
            return 0
        method = message.get("method")
        message_id = message.get("id")

        if method == "initialize":
            if script.get("fail_initialize"):
                send({"jsonrpc": "2.0", "id": message_id, "error": {"code": -32000, "message": "cassé"}})
                return 3
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "agentCapabilities": {"loadSession": False, "promptCapabilities": {"image": False}},
                        "agentInfo": {"name": "fake-agent", "version": "1.0.0"},
                    },
                }
            )
            continue

        if method == "session/new":
            send({"jsonrpc": "2.0", "id": message_id, "result": {"sessionId": "sess-fake-1"}})
            continue

        if method == "session/prompt":
            prompts_seen += 1
            plan = _plan_for(script, prompts_seen)
            session_id = (message.get("params") or {}).get("sessionId", "sess-fake-1")

            for note in plan.get("messages", []):
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": note},
                            },
                        },
                    }
                )

            for write in plan.get("writes", []):
                outgoing_id += 1
                response = request(
                    outgoing_id,
                    "session/request_permission",
                    {
                        "sessionId": session_id,
                        "toolCall": {
                            "toolCallId": f"tc-{outgoing_id}",
                            "title": f"écrire {write['path']}",
                            "kind": "edit",
                            "rawInput": {"path": write["path"]},
                        },
                        "options": [
                            {"optionId": "allow_once", "name": "Autoriser", "kind": "allow_once"},
                            {"optionId": "reject_once", "name": "Refuser", "kind": "reject_once"},
                        ],
                    },
                )
                allowed = _is_allowed(response)
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "tool_call_update",
                                "toolCallId": f"tc-{outgoing_id}",
                                "status": "completed" if allowed else "failed",
                            },
                        },
                    }
                )
                if allowed:
                    target = workspace / write["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(write.get("content", ""), encoding="utf-8")

            for command in plan.get("commands", []):
                outgoing_id += 1
                request(
                    outgoing_id,
                    "session/request_permission",
                    {
                        "sessionId": session_id,
                        "toolCall": {
                            "toolCallId": f"tc-{outgoing_id}",
                            "title": command,
                            "kind": "execute",
                            "rawInput": {"command": command},
                        },
                        "options": [],
                    },
                )

            if "result" in plan:
                result_path = workspace / ".choregos" / "result.json"
                result_path.parent.mkdir(parents=True, exist_ok=True)
                content = plan["result"]
                result_path.write_text(
                    content
                    if isinstance(content, str)
                    else json.dumps(content, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {"stopReason": plan.get("stop_reason", "end_turn")},
                }
            )
            continue

        if method == "session/cancel":
            return 0

        if message_id is not None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": f"méthode inconnue : {method}"},
                }
            )
    return 0


def _plan_for(script: dict[str, Any], prompt_index: int) -> dict[str, Any]:
    turns: list[dict[str, Any]] = script.get("turns", [])
    if not turns:
        return {}
    index = min(prompt_index, len(turns)) - 1
    return turns[index]


def _is_allowed(response: dict[str, Any] | None) -> bool:
    if response is None or "result" not in response:
        return False
    outcome = (response["result"] or {}).get("outcome") or {}
    return str(outcome.get("optionId", "")).startswith("allow")


if __name__ == "__main__":
    raise SystemExit(main())

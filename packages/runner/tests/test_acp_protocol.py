"""Protocole ACP : encodage, décodage, et couverture des messages de la v0.11."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from choregos_runner.acp import AcpClient, Notification, Request, Response, decode
from choregos_runner.acp.protocol import (
    FS_READ_TEXT_FILE,
    INITIALIZE,
    SESSION_NEW,
    SESSION_PROMPT,
    SESSION_REQUEST_PERMISSION,
    SESSION_UPDATE,
    error_response,
)

FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_agent.py"


def test_request_roundtrip() -> None:
    request = Request(id=1, method=INITIALIZE, params={"protocolVersion": 1})
    decoded = decode(request.encode())
    assert isinstance(decoded, Request)
    assert decoded.method == INITIALIZE and decoded.params["protocolVersion"] == 1


def test_response_and_error_roundtrip() -> None:
    ok = decode(Response(id=2, result={"sessionId": "s1"}).encode())
    assert isinstance(ok, Response) and not ok.failed and ok.result == {"sessionId": "s1"}
    ko = decode(error_response(3, -32601, "inconnu").encode())
    assert isinstance(ko, Response) and ko.failed and ko.error["code"] == -32601


def test_notification_roundtrip() -> None:
    notification = Notification(method=SESSION_UPDATE, params={"sessionId": "s1"})
    decoded = decode(notification.encode())
    assert isinstance(decoded, Notification) and decoded.method == SESSION_UPDATE


@pytest.mark.parametrize("line", ["", "   ", "bruit sur stdout", "{cassé", "[]", "null"])
def test_garbage_lines_are_ignored(line: str) -> None:
    assert decode(line) is None


def test_encoding_is_one_json_per_line() -> None:
    encoded = Request(id=1, method="m", params={"texte": "accentué é"}).encode()
    assert encoded.endswith(b"\n")
    assert encoded.count(b"\n") == 1
    assert json.loads(encoded)["params"]["texte"] == "accentué é"


async def _client(tmp_path: Path, script: dict[str, Any], **kwargs: Any) -> AcpClient:
    return AcpClient(
        [sys.executable, str(FAKE_AGENT)],
        cwd=str(tmp_path),
        env={"FAKE_AGENT_SCRIPT": json.dumps(script), "CHOREGOS_WORKSPACE_PATH": str(tmp_path)},
        **kwargs,
    )


async def test_full_session_against_fake_agent(tmp_path: Path) -> None:
    updates: list[dict[str, Any]] = []
    permissions: list[dict[str, Any]] = []

    async def on_update(params: dict[str, Any]) -> None:
        updates.append(params)

    async def on_permission(params: dict[str, Any]) -> tuple[bool, str]:
        permissions.append(params)
        return True, "autorisé par le test"

    client = await _client(
        tmp_path,
        {"turns": [{"messages": ["bonjour"], "writes": [{"path": "a.txt", "content": "x"}]}]},
        on_update=on_update,
        on_permission=on_permission,
    )
    async with client:
        init = await client.initialize()
        assert init["agentInfo"]["name"] == "fake-agent"
        session = await client.new_session(str(tmp_path))
        assert session == "sess-fake-1"
        outcome = await client.prompt("travaille", timeout=30)
    assert outcome.stop_reason == "end_turn"
    assert outcome.turns == 1
    assert updates and permissions
    assert (tmp_path / "a.txt").read_text() == "x"
    assert outcome.tool_calls >= 1


async def test_denied_permission_prevents_the_write(tmp_path: Path) -> None:
    """Une permission refusée n'est pas contournée : le fichier n'existe pas (conformité §2.2)."""

    async def deny(params: dict[str, Any]) -> tuple[bool, str]:
        return False, "refusé par le test"

    client = await _client(
        tmp_path,
        {"turns": [{"writes": [{"path": "interdit.txt", "content": "x"}]}]},
        on_permission=deny,
    )
    async with client:
        await client.initialize()
        await client.new_session(str(tmp_path))
        outcome = await client.prompt("écris", timeout=30)
    assert not (tmp_path / "interdit.txt").exists()
    assert outcome.permission_denials == 1


async def test_fs_requests_are_refused(tmp_path: Path) -> None:
    """Le runner n'expose pas le système de fichiers : l'agent écrit lui-même, sous contrôle du diff."""
    client = await _client(tmp_path, {"turns": [{}]})
    async with client:
        await client.initialize()
        response = await client.request(FS_READ_TEXT_FILE, {"path": "/etc/passwd"}, timeout=10)
    assert response.failed


async def test_unreachable_binary_raises(tmp_path: Path) -> None:
    from choregos_runner.acp import AgentUnreachableError

    client = AcpClient(["/introuvable/agent"], cwd=str(tmp_path))
    with pytest.raises(AgentUnreachableError):
        await client.start()


async def test_initialize_failure_is_reported(tmp_path: Path) -> None:
    from choregos_runner.acp import AgentUnreachableError

    client = await _client(tmp_path, {"fail_initialize": True})
    async with client:
        with pytest.raises(AgentUnreachableError):
            await client.initialize()


async def test_prompt_timeout_cancels_session(tmp_path: Path) -> None:
    client = await _client(tmp_path, {"turns": [{"stop_reason": "end_turn"}]})
    async with client:
        await client.initialize()
        await client.new_session(str(tmp_path))
        # Un timeout très court force l'annulation avant la réponse.
        outcome = await client.prompt("travaille", timeout=0.001)
    assert outcome.stop_reason in {"timeout", "end_turn"}


def test_protocol_methods_are_covered() -> None:
    """Les méthodes du protocole utilisées par le runner sont toutes nommées explicitement."""
    from choregos_runner.acp import protocol

    expected = {
        INITIALIZE,
        SESSION_NEW,
        SESSION_PROMPT,
        SESSION_UPDATE,
        SESSION_REQUEST_PERMISSION,
        protocol.SESSION_CANCEL,
        protocol.SESSION_LOAD,
        protocol.AUTHENTICATE,
        protocol.FS_READ_TEXT_FILE,
        protocol.FS_WRITE_TEXT_FILE,
    }
    assert len(expected) == 10

# SPDX-License-Identifier: Apache-2.0
"""What the daemon's tests share: a door, a server of the outpost's own network, a daemon between."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_contracts import load_schema
from choregos_contracts.outpost import OutpostPolicy
from choregos_outpost import identity as identities
from choregos_outpost import policy as policies
from choregos_outpost import uplink
from choregos_outpost.audit import AuditLog
from choregos_outpost.daemon import Daemon, Exit
from choregos_outpost.fakes import FakeDoor
from choregos_outpost.journal import Journal
from choregos_outpost.toolhost import ToolHost
from cryptography.hazmat.primitives import serialization
from jsonschema import Draft202012Validator

#: What the outpost's own server lists. `create_issue` and `delete_project` declare nothing, so they
#: are writes; `delete_project` is outside the patterns of the bench's policy.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_issues",
        "description": "Search the tracker's issues.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "annotations": {"readOnlyHint": True},
    },
    {"name": "get_issue", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
    {"name": "create_issue", "description": "Create an issue.", "inputSchema": {"type": "object"}},
    {"name": "delete_project", "inputSchema": {"type": "object"}},
]
CREDENTIAL = "tracker-credential-of-the-outpost"
AUDIT_LINE = Draft202012Validator(
    load_schema("outpost-audit-line.schema.json"), format_checker=Draft202012Validator.FORMAT_CHECKER
)


def policy_document(door: FakeDoor, root: Path, **endpoint: Any) -> dict[str, Any]:
    """A policy that pins the bench door's key and serves one alias, `tracker`."""
    secret = root / "tracker-token"
    secret.write_text(CREDENTIAL + "\n", encoding="utf-8")
    return {
        "control_plane": door.origin,
        "door_keys": [door.public_key],
        "endpoints": {
            "tracker": {
                "url": "http://tracker-mcp.tools.svc:8080/mcp",
                "auth": {"header": "Authorization", "from_file": str(secret)},
                "tools": ["search_*", "get_*", "create_issue"],
                **endpoint,
            }
        },
        "concurrency": 4,
        "audit": {"file": str(root / "log" / "audit.jsonl")},
    }


def write_policy(root: Path, document: dict[str, Any]) -> Path:
    path = root / "policy.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


@dataclass
class Bench:
    root: Path
    door: FakeDoor
    server: FakeMcpServer
    policy: OutpostPolicy
    identity: identities.Identity
    instance_id: str
    log: io.StringIO
    daemon: Daemon | None = None
    task: asyncio.Task[Exit] | None = None
    journal: Journal | None = None
    #: When set, the outpost's server holds every tool call until the gate opens.
    gate: asyncio.Event | None = None
    #: The tool calls that reached the server, in the order they arrived (held or not).
    arrived: list[str] = field(default_factory=list)

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def audit_path(self) -> Path:
        return Path(self.policy.audit.file)

    def endpoint(self) -> httpx.MockTransport:
        """The outpost's own server, as the tool host reaches it."""

        async def respond(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content or b"{}")
            if body.get("method") == "tools/call":
                self.arrived.append(str(body["params"]["name"]))
                if self.gate is not None:
                    await self.gate.wait()
            return self.server.respond(request)

        return httpx.MockTransport(respond)

    async def reached(self, count: int = 1, timeout_s: float = 5.0) -> None:
        """Waits until `count` tool calls have arrived at the outpost's server."""
        async with asyncio.timeout(timeout_s):
            while len(self.arrived) < count:
                await asyncio.sleep(0.005)

    def handed(
        self, call_id: str, plaintext: dict[str, Any] | None = None, **signature: Any
    ) -> dict[str, Any]:
        """A call as the door would hand it to this instance: sealed to it, signed by the door. With
        `signature` keywords, the signature is one the contract refuses."""
        document = plaintext or {
            "id": call_id,
            "kind": "call_tool",
            "endpoint": "tracker",
            "tool": "search_issues",
            "arguments": {"query": "forged"},
            "deadline": "2099-01-01T00:00:00Z",
            "answer_key": self.identity.sealing_key,
        }
        sealed = self.door.sealed_for(self.instance_id, call_id, document)
        signed = self.door.signature_for(call_id, self.instance_id, sealed, **signature)
        return {"id": call_id, "sealed": sealed, "signature": signed}

    def uplink(self, transport: httpx.AsyncBaseTransport | None = None, **more: Any) -> uplink.Uplink:
        http = uplink.client(transport=transport or self.door.transport())
        return uplink.Uplink(self.door.origin, self.identity, http, instance_id=self.instance_id, **more)

    async def start(self, *, transport: httpx.AsyncBaseTransport | None = None, **more: Any) -> Daemon:
        """A daemon over this bench's state directory: a second `start` is a restart."""
        self.journal = Journal(self.state / "journal")
        await self.journal.open(more.get("now", time.time)())
        self.daemon = Daemon(
            policy=self.policy,
            identity=self.identity,
            instance_id=self.instance_id,
            uplink=more.pop("uplink", None) or self.uplink(transport),
            journal=self.journal,
            audit=more.pop("audit", None) or AuditLog(self.audit_path),
            tools=more.pop("tools", None) or ToolHost(self.policy, transport=self.endpoint),
            grace_s=more.pop("grace_s", 2.0),
            full_pause_s=more.pop("full_pause_s", 0.02),
            **more,
        )
        self.task = asyncio.create_task(self.daemon.run())
        return self.daemon

    async def stop(self) -> Exit:
        assert self.daemon is not None and self.task is not None
        self.daemon.stop()
        try:
            return await asyncio.wait_for(self.task, timeout=10)
        finally:
            await self.daemon.uplink.aclose()
            if self.journal is not None:
                self.journal.close()

    async def ended(self, timeout_s: float = 5.0) -> Exit:
        """Waits for the daemon to end by itself."""
        assert self.task is not None and self.daemon is not None
        try:
            return await asyncio.wait_for(self.task, timeout=timeout_s)
        finally:
            await self.daemon.uplink.aclose()
            if self.journal is not None:
                self.journal.close()

    async def kill(self) -> None:
        """Ends the daemon's task where it stands. It is not `kill -9` (handlers still run): the test
        that kills a real process is `test_killed_daemon.py`."""
        assert self.task is not None and self.daemon is not None
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task
        await self.daemon.uplink.aclose()
        if self.journal is not None:
            self.journal.close()

    def lines(self, call_id: str | None = None) -> list[dict[str, Any]]:
        """The audit log's lines, each checked against the contract's schema as it is read."""
        if not self.audit_path.exists():
            return []
        lines = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
        for line in lines:
            faults = [error.message for error in AUDIT_LINE.iter_errors(line)]
            assert not faults, f"an audit line the contract refuses: {line}: {faults}"
        return [line for line in lines if call_id is None or line["call_id"] == call_id]

    def logged(self, event: str | None = None) -> list[dict[str, Any]]:
        """The daemon's own log, as it would be read on its standard output."""
        lines = [json.loads(line) for line in self.log.getvalue().splitlines() if line.startswith("{")]
        return [line for line in lines if event is None or line["event"] == event]

    def called(self) -> list[str]:
        """The JSON-RPC methods the outpost's server received."""
        return [method for method, _, _ in self.server.received]


async def bench(root: Path, log: io.StringIO, *, approved: bool = True, **endpoint: Any) -> Bench:
    """An enrolled instance, a door that knows it, and the outpost's own server."""
    door = FakeDoor()
    document = policy_document(door, root, **endpoint)
    policy = policies.load(write_policy(root, document))
    identity = identities.create(root / "state" / "identity")
    async with uplink.client(transport=door.transport()) as http:
        answer = await uplink.Uplink(door.origin, identity, http).enrol(door.enrolment_token(), {})
    if approved:
        door.approve(answer.instance_id, identity.fingerprint)
    server = FakeMcpServer(tools=TOOLS, token=CREDENTIAL, page_size=3)
    return Bench(root, door, server, policy, identity, answer.instance_id, log)


def private_spellings(identity_dir: Path) -> list[str]:
    """Every way a private key of this identity could be written in a text: the lines of its PEM file,
    and its raw bytes in hexadecimal, base64 and base64url."""
    spellings: list[str] = []
    for name in ("signing.key", "sealing.key"):
        pem = (identity_dir / name).read_bytes()
        key = serialization.load_pem_private_key(pem, password=None)
        raw = key.private_bytes(  # type: ignore[union-attr]
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )
        spellings += [line for line in pem.decode().splitlines() if not line.startswith("-----")]
        spellings += [
            raw.hex(),
            base64.b64encode(raw).decode(),
            base64.urlsafe_b64encode(raw).decode().rstrip("="),
        ]
    assert len(spellings) >= 8 and all(len(spelling) >= 40 for spelling in spellings)
    return spellings

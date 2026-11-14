"""A tool call through an outpost, end to end: the real daemon at the real door (S65-19, ADRs 0147-0149).

Each half was tested against a stand-in for the other: the door against a scripted daemon
(`apps/api/tests/kits/outpost_daemon.py`), the daemon against a scripted door
(`choregos_outpost.fakes.door.FakeDoor`). Here they meet, and nothing of either is scripted:

- **the platform** is the API application of the end-to-end bench with outposts switched on: its door
  is mounted, it signs every call it hands over with a key derived from a label, and a call's
  arguments wait sealed under a key wrapper derived from another. A run's calls take the product's own
  path: the broker, the governed action played by the platform's workers on the Temporal test
  server, the rendezvous;
- **the outpost** is the `choregos_outpost` daemon, built from its classes exactly as
  `choregos-outpost enrol` and `choregos-outpost run` build it (its command line is not run here: it
  configures the process's logging), with its keys, its journal and its audit log on disk. Its HTTP
  client reaches the API application through an ASGI transport, under the door's public origin; its
  tool host reaches a fake MCP server behind the alias, with the credential its policy file names;
- **a person** creates the outpost, issues the token, approves the instance against the fingerprint
  the daemon printed, declares the connector and opens its operations, through the API.

What this does not show is anything of a network: no socket, no proxy, no NetworkPolicy. That is
`tests/cluster/test_outpost_tools_on_cluster.py`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from .conftest import Platform, login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

ORG = "/api/v1/orgs/varga"
SLUG = "factory-floor"
OUTPOST = f"{ORG}/outposts/{SLUG}"
ALIAS = "tracker"
CONNECTOR = "tracker"
RUN = "run-outpost-e2e"
TOOLS = f"/api/v1/internal/runs/{RUN}/tools"
SEARCH = f"{TOOLS}/{CONNECTOR}__search_issues"
CREATE = f"{TOOLS}/{CONNECTOR}__create_issue"
ARGUMENTS = {"query": "door stuck"}
TITLE = {"title": "door stuck"}
#: The door's public origin: what the deployment declares and what the outpost's policy file names.
ORIGIN = "https://outposts.example.com"
#: The address of the server behind the alias. It is in the outpost's policy file, and nowhere else.
SERVER_URL = "http://tracker-mcp.tools.svc:8080/mcp"
#: The connector's timeout for everything a test does not measure: a loaded runner is slow.
SETUP_TIMEOUT_S = 30
#: How long a test waits for what happens in the background. Never what it measures.
PATIENCE_S = 60.0
#: What the server answers a search: more than the platform lets an agent read (`bounded_result`).
LONG_PAGE = "OPS-1 the door is stuck; OPS-2 the door is still stuck. " * 3000


def _seed(label: str) -> bytes:
    """32 bytes derived from a label: the test's keys are never written in the repository."""
    return hashlib.sha256(f"choregos e2e, outposts: {label}".encode()).digest()


def _door_key(label: str) -> Any:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.from_private_bytes(_seed(label))


def _pem(label: str) -> str:
    from cryptography.hazmat.primitives import serialization

    return (
        _door_key(label)
        .private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        .decode()
    )


def _public(label: str) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = (
        _door_key(label).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


#: The key the door signs with, and the one the deployment announces beside it for a rotation.
SIGNING = "the door's signing key"
NEXT = "the door's next key"
#: What opens the server behind the alias: the outpost's, in a file its policy names.
CREDENTIAL = _seed("the credential of the tracker's server").hex()

#: What the server behind the alias lists. `create_issue` and `delete_project` declare nothing, so
#: they are writes; `delete_project` is outside the alias's patterns and never leaves the outpost.
SERVED_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_issues",
        "description": "the issues that match",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "get_issue",
        "description": "one issue",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}},
    },
    {
        "name": "create_issue",
        "description": "opens an issue",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}},
    },
    {"name": "delete_project", "description": "deletes a project", "inputSchema": {"type": "object"}},
]
PATTERNS = ["search_*", "get_*", "create_issue"]


# ───────────────────────── the daemon's own log ─────────────────────────


class DaemonLog:
    """The daemon's own log, as the lines it would print: event and fields. It stands in for the
    module's logger, so that nothing of the process's logging is configured for it."""

    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def _say(self, level: str, event: str, fields: dict[str, Any]) -> None:
        self.lines.append({"event": event, "level": level, **fields})

    def debug(self, event: str, **fields: Any) -> None:
        self._say("debug", event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._say("info", event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._say("warning", event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._say("error", event, fields)

    def said(self, event: str) -> list[dict[str, Any]]:
        return [line for line in self.lines if line["event"] == event]


@pytest.fixture(autouse=True)
def outposts_on(monkeypatch: pytest.MonkeyPatch) -> DaemonLog:
    """Outposts on, before the application is made: the door is mounted, it has an origin, a key that
    signs and one announced beside it; the deployment has a key wrapper; a wait looks often, since
    SQLite has no relay to wake it; and the daemon's log is read here."""
    from choregos_api.outposts import rendezvous
    from choregos_api.secret_store import ENV_KEKS
    from choregos_outpost import daemon

    monkeypatch.setenv("CHOREGOS_OUTPOSTS", "on")
    monkeypatch.setenv("CHOREGOS_OUTPOSTS_DOOR_URL", ORIGIN)
    monkeypatch.setenv("CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY", _pem(SIGNING))
    monkeypatch.setenv("CHOREGOS_OUTPOSTS_DOOR_ANNOUNCED_KEYS", json.dumps([_public(NEXT)]))
    # The rate per instance is the door's, per replica; the tests poll as often as they need.
    monkeypatch.setenv("CHOREGOS_OUTPOSTS_DOOR_RATE_PER_MINUTE", "0")
    monkeypatch.setenv(ENV_KEKS, f"k1:{base64.b64encode(_seed('the key wrapper')).decode()}")
    monkeypatch.setattr(rendezvous, "LOOK_S", (0.05, 0.1))
    said = DaemonLog()
    monkeypatch.setattr(daemon, "log", said)
    return said


# ───────────────────────── the outpost's side ─────────────────────────


class Server:
    """The MCP server behind the alias, in the outpost's own network. It counts the tool calls that
    arrive, and holds each one while `gate` is closed."""

    def __init__(self) -> None:
        from choregos_adapters.fakes.mcp import FakeMcpServer

        self.mcp = FakeMcpServer(
            tools=[dict(tool) for tool in SERVED_TOOLS],
            token=CREDENTIAL,
            answers={"search_issues": LONG_PAGE},
            structured={"create_issue": {"key": "OPS-7"}},
        )
        #: The tool calls that arrived, in their order, answered or not.
        self.arrived: list[str] = []
        self.gate: asyncio.Event | None = None

    def transport(self) -> httpx.AsyncBaseTransport:
        """What the daemon's tool host reaches the server with, one transport per call."""

        async def respond(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content or b"{}")
            if body.get("method") == "tools/call":
                self.arrived.append(str(body["params"]["name"]))
                if self.gate is not None:
                    await self.gate.wait()
            return self.mcp.respond(request)

        return httpx.MockTransport(respond)

    def credentials(self) -> set[str]:
        """The `Authorization` headers the server was opened with."""
        return {headers.get("authorization", "") for _, headers, _ in self.mcp.received}


class Wire(httpx.AsyncBaseTransport):
    """The daemon's HTTP client's transport: the API application, reached in process. It notes what
    the door answered each request: its path, its status and, for a problem, its code."""

    def __init__(self, app: Any, every: list[tuple[str, int, str | None]]) -> None:
        self._inner = httpx.ASGITransport(app=app)
        #: What this client was answered.
        self.seen: list[tuple[str, int, str | None]] = []
        #: What every client of the outpost was answered, across its restarts.
        self._every = every

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        body = await response.aread()
        code = None
        if response.headers.get("content-type", "").startswith("application/problem+json"):
            code = json.loads(body).get("code")
        self.seen.append((request.url.path, response.status_code, code))
        self._every.append(self.seen[-1])
        return httpx.Response(response.status_code, headers=response.headers, content=body)


@dataclass
class Outpost:
    """One instance of `choregos-outpost`, on its own state directory, as its operator runs it."""

    root: Path
    app: Any
    log: DaemonLog
    server: Server = field(default_factory=Server)
    wire: Wire | None = None
    #: What the door answered the outpost, across its restarts.
    exchanges: list[tuple[str, int, str | None]] = field(default_factory=list)
    instance_id: str | None = None
    daemon: Any = None
    task: asyncio.Task[Any] | None = None
    journal: Any = None

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def audit_path(self) -> Path:
        return self.root / "log" / "audit.jsonl"

    def endpoint(self, **changes: Any) -> dict[str, Any]:
        """The alias as the operator writes it: the server's address and the file of its credential."""
        secret = self.root / "secrets" / "tracker"
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text(CREDENTIAL + "\n", encoding="utf-8")
        return {
            "url": SERVER_URL,
            "auth": {"header": "Authorization", "from_file": str(secret)},
            "tools": list(PATTERNS),
            "writes": "allow",
            **changes,
        }

    def policy(self, given: dict[str, Any], *, endpoints: dict[str, Any] | None = None, **more: Any) -> Any:
        """The policy file, written from what the console gave with the token (the door's origin and
        its keys to pin), and read as the daemon reads it."""
        from choregos_outpost import policy as policies

        document = {
            "control_plane": given["control_plane"],
            "door_keys": list(given["door_keys"]),
            "endpoints": {ALIAS: self.endpoint()} if endpoints is None else endpoints,
            "concurrency": 4,
            "audit": {"file": str(self.audit_path)},
        } | more
        path = self.root / "policy.yaml"
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
        return policies.load(path)

    def http(self) -> httpx.AsyncClient:
        from choregos_outpost import uplink

        self.wire = Wire(self.app, self.exchanges)
        return uplink.client(transport=self.wire)

    async def enrol(self, policy: Any, token: str) -> str:
        """`choregos-outpost enrol`: makes the keys, presents the token, keeps what the door answered,
        and returns the fingerprint it prints, computed from its own key."""
        from choregos_outpost import identity as identities
        from choregos_outpost import uplink

        directory = self.state / "identity"
        own, new = identities.load_or_create(directory)
        assert new, "the keys were made on this machine"
        async with self.http() as http:
            answer = await uplink.Uplink(policy.control_plane, own, http).enrol(token, {})
        identities.save_enrolment(
            directory,
            identities.Enrolment(
                instance_id=answer.instance_id,
                organisation=answer.organisation,
                outpost=answer.outpost,
                control_plane=policy.control_plane,
                enrolled_at=datetime.now(UTC).isoformat(),
            ),
        )
        assert (answer.organisation, answer.outpost, answer.status) == ("varga", SLUG, "pending")
        assert answer.fingerprint == own.fingerprint, "nothing on the path rewrote the enrolment"
        self.instance_id = answer.instance_id
        return str(own.fingerprint)

    async def run(self, policy: Any, *, grace_s: float = 20.0) -> None:
        """`choregos-outpost run`: what `cli._serve` builds, on this instance's state directory. A
        second `run` after a `stop` is a restart."""
        from choregos_outpost import identity as identities
        from choregos_outpost import uplink
        from choregos_outpost.audit import AuditLog
        from choregos_outpost.daemon import Daemon
        from choregos_outpost.journal import Journal
        from choregos_outpost.toolhost import ToolHost

        directory = self.state / "identity"
        own = identities.load(directory)
        enrolment = identities.load_enrolment(directory)
        assert enrolment is not None, "the keys enrolled"
        self.journal = Journal(self.state / "journal")
        audit = AuditLog(Path(policy.audit.file))
        await self.journal.open(time.time())
        await audit.check()
        door = uplink.Uplink(
            policy.control_plane,
            own,
            self.http(),
            instance_id=enrolment.instance_id,
            sequence=uplink.Sequence(self.state / "journal" / "sequence"),
        )
        self.daemon = Daemon(
            policy=policy,
            identity=own,
            instance_id=enrolment.instance_id,
            uplink=door,
            journal=self.journal,
            audit=audit,
            tools=ToolHost(policy, transport=self.server.transport),
            grace_s=grace_s,
        )
        self.task = asyncio.create_task(self.daemon.run())

    async def restart(self, policy: Any, **more: Any) -> None:
        """Stopped, and started again on the same state directory: how a new policy file is read."""
        await self.stop()
        await self.run(policy, **more)

    def met_its_own_poll(self) -> bool:
        """Whether a poll of this instance ever met another one under its key: a former poll of a
        daemon that stopped, which the door should have let go of (ADR 0148 §7)."""
        conflicts = [seen for seen in self.exchanges if seen[2] == "poll_conflict"]
        return bool(conflicts or self.log.said("door.poll_conflict"))

    async def stop(self) -> Any:
        """SIGTERM: the polling ends, what the daemon holds has its grace period."""
        assert self.daemon is not None
        self.daemon.stop()
        return await self.ended()

    async def ended(self) -> Any:
        """Waits for the daemon to end, and lets go of its connection and its journal."""
        assert self.task is not None and self.daemon is not None
        try:
            return await asyncio.wait_for(self.task, timeout=PATIENCE_S)
        finally:
            await self.daemon.uplink.aclose()
            self.journal.close()
            self.daemon, self.task = None, None

    def lines(self, call_id: str | None = None) -> list[dict[str, Any]]:
        """The local audit log, each line checked against the contract's schema as it is read."""
        from choregos_contracts import load_schema
        from jsonschema import Draft202012Validator

        if not self.audit_path.exists():
            return []
        schema = Draft202012Validator(
            load_schema("outpost-audit-line.schema.json"), format_checker=Draft202012Validator.FORMAT_CHECKER
        )
        lines = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
        for line in lines:
            faults = [error.message for error in schema.iter_errors(line)]
            assert not faults, f"an audit line the contract refuses: {line}: {faults}"
        return [line for line in lines if call_id is None or line["call_id"] == call_id]


@pytest.fixture
async def outpost(platform: Platform, tmp_path: Path, outposts_on: DaemonLog) -> AsyncIterator[Outpost]:
    bench = Outpost(root=tmp_path / "outpost", app=platform.app, log=outposts_on)
    yield bench
    if bench.task is not None:
        with contextlib.suppress(Exception):
            await bench.stop()


# ───────────────────────── the platform's side ─────────────────────────


async def _until(check: Callable[[], Awaitable[bool]], what: str) -> None:
    deadline = time.monotonic() + PATIENCE_S
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"not within {PATIENCE_S:.0f} seconds: {what}")


async def _seen(client: Any, endpoints: list[str]) -> None:
    """Waits until the platform shows the outpost online, its instances announcing `endpoints`."""

    async def shown() -> bool:
        body = (await client.get(OUTPOST)).json()
        return bool(body["online"]) and body["endpoints"] == endpoints

    await _until(shown, f"the outpost is online for {endpoints}")


@dataclass
class Bench:
    platform: Platform
    outpost: Outpost
    #: What the console gave with the token: the door's origin and the keys to pin.
    given: dict[str, Any]
    fingerprint: str
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def client(self) -> Any:
        return self.platform.client


async def _enrolled_approved_and_running(platform: Platform, outpost: Outpost) -> Bench:
    """A person creates the outpost and issues a token; the operator writes the policy file from what
    the console gave, enrols the instance and starts the daemon; the person approves the instance
    against the fingerprint the daemon printed; the daemon is served without a restart."""
    client = platform.client
    await login(client)
    created = await client.post(f"{ORG}/outposts", json={"slug": SLUG, "display_name": "Factory floor"})
    assert created.status_code == 201, created.text
    assert created.json()["instances"]["pending"] == 0 and not created.json()["online"]
    issued = await client.post(f"{OUTPOST}/enrolments")
    assert issued.status_code == 201, issued.text
    given = issued.json()
    assert given["control_plane"] == ORIGIN
    assert given["door_keys"] == [_public(SIGNING), _public(NEXT)], "the key that signs first"

    policy = outpost.policy(given)
    printed = await outpost.enrol(policy, given["token"])
    (pending,) = (await client.get(f"{OUTPOST}/instances")).json()["items"]
    assert (pending["id"], pending["status"]) == (outpost.instance_id, "pending")
    assert pending["fingerprint"] == printed, "the console shows the fingerprint the daemon printed"

    # Started before anybody approved it: it says so once, with its fingerprint, and keeps asking.
    await outpost.run(policy)
    await _until(lambda: _true(bool(outpost.log.said("outpost.pending"))), "the daemon says it is pending")
    (said,) = outpost.log.said("outpost.pending")
    assert said["fingerprint"] == printed
    # Approved against another fingerprint: refused. Against the one the daemon printed: approved.
    wrong = await client.post(
        f"{OUTPOST}/instances/{outpost.instance_id}/approve",
        json={"fingerprint": "SHA256:" + base64.b64encode(_seed("another key")).decode().rstrip("=")},
    )
    assert wrong.status_code in (409, 422), wrong.text
    approved = await client.post(
        f"{OUTPOST}/instances/{outpost.instance_id}/approve", json={"fingerprint": printed}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"
    await _seen(client, [ALIAS])
    assert len(outpost.log.said("outpost.pending")) == 1, "said once"
    return Bench(platform, outpost, given, printed)


async def _true(value: bool) -> bool:
    return value


async def _declared_discovered_and_opened(bench: Bench, *, writes: dict[str, Any] | None = None) -> None:
    """A connector at the outpost, its tools discovered through it (each born `forbidden`), the read
    opened, and a run of an agent that names the connector."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    client = bench.client
    declared = await client.post(
        f"{ORG}/connectors",
        json={
            "name": CONNECTOR,
            "type": "mcp-outpost",
            "config": {"outpost": SLUG, "endpoint": ALIAS, "timeout_s": SETUP_TIMEOUT_S},
        },
    )
    assert declared.status_code == 201, declared.text
    discovered = await client.post(f"{ORG}/connectors/{CONNECTOR}/discover")
    assert discovered.status_code == 200, discovered.text
    operations = (await client.get(f"{ORG}/connectors/{CONNECTOR}")).json()["operations"]
    # What the alias's patterns let through, and nothing beyond: `delete_project` stayed at the outpost.
    assert sorted(op["name"] for op in operations) == ["create_issue", "get_issue", "search_issues"]
    assert {op["policy"] for op in operations} == {"forbidden"}, "every discovered tool is born forbidden"
    opened = await client.patch(
        f"{ORG}/connectors/{CONNECTOR}/operations/search_issues", json={"policy": "allowed"}
    )
    assert opened.status_code == 200, opened.text
    for operation, statement in (writes or {}).items():
        done = await client.patch(f"{ORG}/connectors/{CONNECTOR}/operations/{operation}", json=statement)
        assert done.status_code == 200, done.text

    spec = {"mcp_servers": [{"connector": CONNECTOR, "tools": ["*"]}]}
    agent = await client.post(
        f"{ORG}/agents", json={"slug": "coordinator", "display_name": "Coordinator", "spec": spec}
    )
    assert agent.status_code == 201, agent.text
    async with session_scope() as session:
        item = WorkItem(
            project_id=bench.platform.project_id, tracker_key="varga/billing-api#7", title="T", state="ready"
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=RUN,
                work_item_id=item.id,
                project_id=bench.platform.project_id,
                stage_role="plan",
                transition_id="t-plan",
                status="running",
                agent_slug="coordinator",
                agent_version=1,
                initiated_by="person:admin@varga.dev",
            )
        )
    token = mint_run_token(
        RUN, project_slug=bench.platform.project_slug, work_item_key="varga/billing-api#7", ttl_minutes=30
    )
    bench.headers = {"Authorization": f"Bearer {token}"}


async def _prepared(platform: Platform, outpost: Outpost, **declared: Any) -> Bench:
    bench = await _enrolled_approved_and_running(platform, outpost)
    await _declared_discovered_and_opened(bench, **declared)
    return bench


async def _timeout(bench: Bench, seconds: int) -> None:
    """The connector's timeout from now on: how long a call waits, and how long a lease lasts."""
    edited = await bench.client.patch(
        f"{ORG}/connectors/{CONNECTOR}",
        json={"config": {"outpost": SLUG, "endpoint": ALIAS, "timeout_s": seconds}},
    )
    assert edited.status_code == 200, edited.text


async def _records(bench: Bench) -> list[dict[str, Any]]:
    """What the platform keeps of the calls routed to the outpost, newest first."""
    listed = await bench.client.get(f"{OUTPOST}/calls")
    assert listed.status_code == 200, listed.text
    return [item for item in listed.json()["items"] if item["kind"] == "call_tool"]


async def _ledger() -> list[Any]:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(select(CostLedger).where(CostLedger.provider == f"mcp:{CONNECTOR}"))
        return list(rows.scalars())


async def _called_events() -> list[Any]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(select(Event).where(Event.type == "choregos.tool.called"))
        return [
            event for event in rows.scalars() if event.payload.get("tool") == f"{CONNECTOR}__search_issues"
        ]


def _refused(answer: dict[str, Any], status: int, code: str) -> dict[str, Any]:
    """A tool error that is the outpost's: its status, its code, the outpost and the alias named."""
    assert answer["status_code"] == status, answer
    body = dict(answer["result"])
    assert (body["code"], body["outpost"], body["endpoint"]) == (code, SLUG, ALIAS), body
    assert SLUG in body["error"], "the refusal names the outpost"
    return body


async def _search(bench: Bench) -> dict[str, Any]:
    answered = await bench.client.post(SEARCH, headers=bench.headers, json=ARGUMENTS)
    assert answered.status_code == 200, answered.text
    return dict(answered.json())


# ───────────────────────── the read ─────────────────────────


async def test_an_outpost_is_enrolled_approved_and_run_and_a_read_through_it_is_governed_end_to_end(
    platform: Platform, outpost: Outpost
) -> None:
    from choregos_api.services.broker import result_limit
    from choregos_contracts import arguments_sha256

    bench = await _prepared(platform, outpost)
    # The daemon's first signed request carries no nonce yet: refused once, and signed again.
    polls = [(status, code) for path, status, code in (outpost.wire.seen if outpost.wire else [])]
    assert polls[0] == (401, "stale_nonce") and polls[1][1] != "stale_nonce", polls[:3]

    answer = await _search(bench)
    assert answer["status_code"] == 200, answer
    result = answer["result"]
    # One result, bounded at the platform after it crossed the outpost whole.
    served = {"content": [{"type": "text", "text": LONG_PAGE}], "isError": False}
    assert result["truncated"]["total_bytes"] == len(json.dumps(served, ensure_ascii=False).encode())
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= result_limit()
    assert result["content"][0]["text"].startswith(LONG_PAGE[:200])
    # The server received the call once, opened with the outpost's credential.
    assert outpost.server.mcp.calls == [("search_issues", ARGUMENTS)]
    assert outpost.server.credentials() == {f"Bearer {CREDENTIAL}"}
    # One ledger line, under the connector.
    (line,) = await _ledger()
    assert (line.kind, line.run_id, line.model) == ("tool", RUN, f"{CONNECTOR}__search_issues")

    # The two logs, compared: the platform's record and the outpost's own name the same call, with
    # the same digest of the arguments, which is also the one the `tool.called` event keeps.
    (record,) = await _records(bench)
    assert (record["state"], record["outcome"], record["tool"], record["endpoint"]) == (
        "answered",
        "result",
        "search_issues",
        ALIAS,
    )
    assert (record["instance_id"], record["run_id"], record["project"]) == (
        outpost.instance_id,
        RUN,
        "billing-api",
    )
    received, executed = outpost.lines(record["id"])
    assert (received["event"], executed["event"]) == ("received", "executed")
    assert executed["arguments_sha256"] == record["arguments_sha256"] == arguments_sha256(ARGUMENTS)
    (event,) = await _called_events()
    assert event.payload["request_sha256"] == record["arguments_sha256"]
    assert (executed["endpoint"], executed["tool"], executed["outcome"], executed["write"]) == (
        ALIAS,
        "search_issues",
        "result",
        False,
    )
    assert executed["signed_by"] == _public(SIGNING), "the call was signed by the key the door signs with"
    asserted = executed["asserted"]
    assert (asserted["run"], asserted["project"], asserted["connector"], asserted["org"]) == (
        RUN,
        "billing-api",
        CONNECTOR,
        "varga",
    )
    # Digests only, on both sides: neither log holds the arguments or the result.
    for written in (json.dumps(outpost.lines()), json.dumps(record)):
        assert ARGUMENTS["query"] not in written and "OPS-1" not in written

    # The address and the credential behind the alias exist at the outpost only.
    shown = json.dumps(
        [
            (await bench.client.get(f"{ORG}/connectors/{CONNECTOR}")).json(),
            (await bench.client.get(OUTPOST)).json(),
            await _records(bench),
        ]
    )
    assert SERVER_URL not in shown and "tracker-mcp" not in shown and CREDENTIAL not in shown


# ───────────────────────── the daemon stopped ─────────────────────────


async def test_with_the_daemon_stopped_a_call_fails_fast_and_names_the_outpost(
    platform: Platform, outpost: Outpost
) -> None:
    from choregos_api.db.models import OutpostInstance
    from choregos_api.services.outposts import POLL_WINDOW_S

    bench = await _prepared(platform, outpost)
    assert (await outpost.stop()).name == "STOPPED"
    assert outpost.log.said("outpost.stopped")[-1]["reason"] == "stopped"

    # Within a minute of its last poll the instance still counts as there: the call is placed, nobody
    # claims it, and it fails at the connector's timeout, as offline, having reached no server.
    await _timeout(bench, 2)
    started = time.monotonic()
    body = _refused(await _search(bench), 503, "outpost_offline")
    assert time.monotonic() - started < 2 + PATIENCE_S / 3, "within the connector's timeout"
    assert {record["state"] for record in await _records(bench)} <= {"expired"}
    assert outpost.server.arrived == [] and await _ledger() == [], (
        "nothing reached the server, nothing booked"
    )
    assert "type" in body, "a refusal that reached no server is not booked"

    # Once that minute has passed (the row is aged as time would have aged it), the call fails at
    # once: nothing is written for an outpost nobody polls, and the connector's timeout is not waited.
    await _timeout(bench, SETUP_TIMEOUT_S)
    long_ago = datetime.now(UTC) - timedelta(seconds=POLL_WINDOW_S + 5)
    await _age(str(outpost.instance_id), OutpostInstance, last_seen_at=long_ago)
    placed = len(await _records(bench))
    started = time.monotonic()
    body = _refused(await _search(bench), 503, "outpost_offline")
    assert time.monotonic() - started < SETUP_TIMEOUT_S, "it did not wait for a claim that could not come"
    assert "is offline" in body["error"] and f"{POLL_WINDOW_S} seconds" in body["error"], body
    assert len(await _records(bench)) == placed, "nothing was written"
    assert not (await bench.client.get(OUTPOST)).json()["online"]


async def _age(row_id: str, model: Any, **values: Any) -> None:
    """Writes a row of the outpost by hand: what time would have done."""
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    async with session_scope() as session:
        done = await session.execute(update(model).where(model.id == row_id).values(**values))
        assert done.rowcount == 1  # type: ignore[attr-defined]


# ───────────────────────── the outpost's own policy ─────────────────────────


async def test_an_alias_removed_from_the_policy_is_refused_before_anything_reaches_the_outpost(
    platform: Platform, outpost: Outpost
) -> None:
    """The daemon announces the aliases its file holds at every poll, and the door hands an instance
    only the calls for those: a call for an alias the file no longer holds never reaches the
    outpost. It is refused at the platform, which names the alias, and the local log has no line of
    it, because there was no call to refuse there (`unknown_endpoint` is never reached this way)."""
    bench = await _prepared(platform, outpost)
    before = len(outpost.lines())
    await outpost.restart(outpost.policy(bench.given, endpoints={}))
    await _seen(bench.client, [])
    assert not outpost.met_its_own_poll(), "the door let go of the poll of the daemon that stopped"

    body = _refused(await _search(bench), 503, "outpost_offline")
    assert f"no instance of it that serves `{ALIAS}`" in body["error"], body
    assert await _records(bench) == [], "nothing was written for the outpost to claim"
    assert len(outpost.lines()) == before, "the outpost was handed nothing, so it logged nothing"
    assert outpost.server.arrived == []


async def test_a_tool_outside_the_aliass_patterns_is_refused_by_the_outpost_and_its_log_says_so(
    platform: Platform, outpost: Outpost
) -> None:
    """The daemon restarted with a file whose alias no longer lets `search_*` through: the call is
    handed over, refused by the outpost alone with its code, and the two logs say the same."""
    from choregos_contracts import arguments_sha256

    bench = await _prepared(platform, outpost)
    await outpost.restart(outpost.policy(bench.given, endpoints={ALIAS: outpost.endpoint(tools=["get_*"])}))
    await _seen(bench.client, [ALIAS])
    assert not outpost.met_its_own_poll()

    body = _refused(await _search(bench), 422, "outpost_refused")
    assert body["refusal"] == "tool_not_allowed"
    assert outpost.server.arrived == [], "the server received nothing"
    assert await _ledger() == [], "a call that reached no server is not booked"
    (record,) = await _records(bench)
    assert (record["state"], record["refusal"]) == ("refused", "tool_not_allowed")
    received, refused = outpost.lines(record["id"])
    assert (received["event"], refused["event"], refused["refusal"]) == (
        "received",
        "refused",
        "tool_not_allowed",
    )
    assert (refused["tool"], refused["endpoint"]) == ("search_issues", ALIAS)
    assert refused["arguments_sha256"] == record["arguments_sha256"] == arguments_sha256(ARGUMENTS)


async def test_a_call_signed_by_a_key_the_policy_does_not_pin_is_refused_unopened(
    platform: Platform, outpost: Outpost
) -> None:
    """The door signs with a key the outpost's file does not pin (a rotation the operator did not
    follow): the call is refused `signature_refused`, its envelope is never opened, its one local
    line names nothing it held, and the server receives nothing."""
    bench = await _prepared(platform, outpost)
    await outpost.restart(outpost.policy(bench.given | {"door_keys": [_public(NEXT)]}))
    await _seen(bench.client, [ALIAS])
    assert not outpost.met_its_own_poll()

    body = _refused(await _search(bench), 422, "outpost_refused")
    assert body["refusal"] == "signature_refused"
    assert outpost.server.arrived == [] and await _ledger() == []
    (record,) = await _records(bench)
    assert (record["state"], record["refusal"]) == ("refused", "signature_refused")
    (line,) = outpost.lines(record["id"])
    assert (line["event"], line["refusal"]) == ("refused", "signature_refused")
    unopened = {"tool", "endpoint", "arguments_sha256", "kind", "signed_by", "asserted"}
    assert not unopened & set(line), f"the line names something the envelope held: {line}"


async def test_a_daemon_below_the_doors_minimum_protocol_stops_and_says_so(
    platform: Platform, outpost: Outpost, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The platform is upgraded and its door's minimum rises above what the daemon speaks: the daemon
    is handed nothing, says so with the window, and ends with the code that asks for an upgrade."""
    from choregos_api.outposts import auth
    from choregos_outpost import PROTOCOL

    bench = await _prepared(platform, outpost)
    await outpost.stop()
    monkeypatch.setattr(auth, "PROTOCOL_MINIMUM", PROTOCOL + 1)
    monkeypatch.setattr(auth, "PROTOCOL_CURRENT", PROTOCOL + 1)
    await outpost.run(outpost.policy(bench.given))
    ended = await outpost.ended()
    assert (ended.name, int(ended)) == ("UPGRADE_REQUIRED", 3)
    (said,) = outpost.log.said("outpost.protocol_unsupported")
    assert (said["speaks"], said["door_minimum"]) == (PROTOCOL, PROTOCOL + 1)
    assert (
        outpost.wire is not None and ("/outposts/v1/poll", 426, "protocol_unsupported") in outpost.wire.seen
    )
    assert outpost.server.arrived == []


# ───────────────────────── a write whose lease is lost ─────────────────────────


async def test_a_write_whose_lease_is_lost_is_in_doubt_and_the_server_received_one_call(
    platform: Platform, outpost: Outpost, worker: Any
) -> None:
    """An allowed write, opened under `in_doubt_accepted`, called by a run: a governed action that the
    platform's workers play. The instance that claimed it stops in the middle of it, its server
    holding the call: nobody answers before the lease ends, the action ends `in_doubt`, nothing is
    compensated and nothing sends it again. The daemon, started again, reports the call
    `unanswered` in its own log and does not make it again."""
    accepted = {"create_issue": {"policy": "allowed", "in_doubt_accepted": True}}
    bench = await _prepared(platform, outpost, writes=accepted)
    # A grace period shorter than the server holds the write: stopping cuts it.
    await outpost.restart(outpost.policy(bench.given), grace_s=0.5)
    await _seen(bench.client, [ALIAS])
    # The lease is the connector's timeout: long enough for an instance in the same process to claim
    # the call, short enough to wait out.
    await _timeout(bench, 8)
    outpost.server.gate = asyncio.Event()

    async with worker():
        calling = asyncio.create_task(bench.client.post(CREATE, headers=bench.headers, json=TITLE))
        await _until(
            lambda: _true(outpost.server.arrived == ["create_issue"]), "the write reached the server"
        )
        assert (await outpost.stop()).name == "STOPPED"
        assert outpost.log.said("outpost.left_running"), "the daemon cut the write it held"
        answered = await asyncio.wait_for(calling, timeout=PATIENCE_S)
    assert answered.status_code == 200, answered.text
    told = answered.json()
    assert told["status_code"] == 504, told
    assert (told["result"]["status"], told["result"]["code"]) == ("in_doubt", "action_in_doubt")
    assert SLUG in told["result"]["error"]

    assert outpost.server.arrived == ["create_issue"], "the server received exactly one call"
    (record,) = await _records(bench)
    assert (record["state"], record["write"], record["instance_id"]) == (
        "in_doubt",
        True,
        outpost.instance_id,
    )
    action_id = told["result"]["action"]
    shown = (await bench.client.get(f"/api/v1/projects/{platform.project_id}/actions/{action_id}")).json()
    assert shown["status"] == "in_doubt" and shown["result"]["compensations"] == []
    assert [effect["status"] for effect in shown["journal"]] == ["in_doubt"]
    (sent,) = shown["result"]["in_doubt"]["sent"]
    assert (sent["call_id"], sent["instance_id"], sent["operation"]) == (
        record["id"],
        outpost.instance_id,
        "create_issue",
    )
    (received,) = outpost.lines(record["id"])
    assert received["event"] == "received", "taken, begun, and never settled"

    # Started again on the same state directory: the journal holds a write begun and never answered.
    await outpost.run(outpost.policy(bench.given))
    await _seen(bench.client, [ALIAS])
    assert not outpost.met_its_own_poll()
    await _until(
        lambda: _true(len(outpost.lines(record["id"])) == 2), "the restarted daemon reports the call"
    )
    replayed = outpost.lines(record["id"])[-1]
    assert (replayed["event"], replayed["outcome"], replayed["write"]) == ("replayed", "unanswered", True)
    (left,) = outpost.log.said("call.left_unanswered")
    assert (left["call_id"], left["write"]) == (record["id"], True)
    # Its lease is long over: the report stays at the outpost, the door is not asked to take it.
    assert outpost.wire is not None
    assert not [seen for seen in outpost.wire.seen if record["id"] in seen[0]]
    assert outpost.server.arrived == ["create_issue"], "and it was not made again"
    (record,) = await _records(bench)
    assert record["state"] == "in_doubt"


# ───────────────────────── a revoked instance ─────────────────────────


async def test_a_revoked_instance_stops_with_the_code_that_says_so_at_every_start(
    platform: Platform, outpost: Outpost
) -> None:
    """A person revokes the instance: its poll is answered at once, the daemon stops with code 4, and
    a supervisor that starts it again meets the same answer, each time (`docs/outpost-operator.md`,
    exit codes). Nothing is handed to it any more."""
    bench = await _prepared(platform, outpost)
    revoked = await bench.client.post(f"{OUTPOST}/instances/{outpost.instance_id}/revoke")
    assert revoked.status_code == 200, revoked.text
    ended = await outpost.ended()
    assert (ended.name, int(ended)) == ("REVOKED", 4)
    assert outpost.log.said("outpost.revoked")
    for _start in range(2):
        await outpost.run(outpost.policy(bench.given))
        assert int(await outpost.ended()) == 4
    assert len(outpost.log.said("outpost.revoked")) == 3
    body = _refused(await _search(bench), 503, "outpost_offline")
    assert "no instance of it" in body["error"]
    assert outpost.server.arrived == [] and await _records(bench) == []

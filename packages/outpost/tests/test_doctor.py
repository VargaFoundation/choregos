# SPDX-License-Identifier: Apache-2.0
"""`doctor`: one check each, and for each a test that fails it.

The checks meet real sockets where what they look at is one: a door served over TLS under a private
certificate authority, an outbound proxy that opens or refuses a tunnel. Nothing reaches beyond the
loopback.
"""

from __future__ import annotations

import io
import json
import socket
import ssl
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_outpost import doctor
from choregos_outpost import identity as identities
from choregos_outpost import policy as policies
from choregos_outpost.fakes import FakeDoor
from choregos_outpost.settings import Settings
from choregos_outpost.toolhost import ToolHost

from ._bench import CREDENTIAL, TOOLS, policy_document, write_policy
from ._wire import AUTHORITY, closed_port, private_authority, proxy, served


def _by_name(checks: list[doctor.Check]) -> dict[str, doctor.Check]:
    return {check.name: check for check in checks}


async def _resolves(_host: str, _port: int) -> list[str]:
    return ["192.0.2.10"]


# ───────────────────────── every check green, on real sockets ─────────────────────────


async def test_every_check_passes_behind_a_private_authority_the_operator_gave(tmp_path: Path) -> None:
    ca_file, tls = private_authority(tmp_path / "pki")
    door = FakeDoor()
    server = FakeMcpServer(tools=TOOLS, token=CREDENTIAL)
    asked: list[httpx.Headers] = []

    async def seen(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers)
        return await door.handle(request)

    async with served(seen, tls=tls, origin=lambda url: setattr(door, "origin", url)) as origin:
        path = write_policy(tmp_path, policy_document(door, tmp_path))
        identities.create(tmp_path / "state" / "identity")
        settings = Settings(policy=path, state_dir=tmp_path / "state", ca_file=ca_file)
        checks = await doctor.diagnose(settings, endpoints=server.transport)
    found = _by_name(checks)
    assert list(found) == [
        "policy",
        "identity",
        "records",
        "dns",
        "proxy",
        "tls",
        "door",
        "clock",
        "endpoint tracker",
    ]
    assert {name: check.status for name, check in found.items()} == {
        "policy": "ok",
        "identity": "ok",
        "records": "ok",
        "dns": "ok",
        "proxy": "skipped",
        "tls": "ok",
        "door": "ok",
        "clock": "ok",
        "endpoint tracker": "ok",
    }, doctor.render(checks)
    assert AUTHORITY in found["tls"].detail and origin in found["door"].detail
    assert (
        "4 tool(s) listed, 3 within its patterns, 2 of them declared read-only"
        in found["endpoint tracker"].detail
    )
    # It asked the door a request the door refuses: it signed nothing and polled nothing, so it can
    # run beside a daemon without taking its place.
    assert door.received == [("poll", None, "{}")] and door.alerts == []
    assert len(asked) == 1 and "outpost-signature" not in asked[0]
    assert json.loads(doctor.as_json(checks))["ok"] is True


# ───────────────────────── one failing test per check ─────────────────────────


def test_policy_fails_on_an_invalid_file_and_names_the_field(tmp_path: Path) -> None:
    door = FakeDoor()
    document = policy_document(door, tmp_path)
    document["endpoints"]["tracker"]["writes"] = "sometimes"
    check, policy = doctor.policy_check(write_policy(tmp_path, document))
    assert check.failed and policy is None and "endpoints.tracker.writes" in check.detail
    assert "does not start" in check.hint


def test_policy_fails_on_door_keys_that_do_not_parse_as_keys(tmp_path: Path) -> None:
    door = FakeDoor()
    for broken in (["not-a-key"], [door.public_key[:-2]], [door.public_key, "SHA256:" + "A" * 43], []):
        document = policy_document(door, tmp_path) | {"door_keys": broken}
        check, policy = doctor.policy_check(write_policy(tmp_path, document))
        assert check.failed and policy is None, broken
        assert "door_keys" in check.detail and "door_keys" in check.hint, broken
    missing = policy_document(door, tmp_path)
    del missing["door_keys"]
    check, _ = doctor.policy_check(write_policy(tmp_path, missing))
    assert check.failed and "door_keys: is required" in check.detail


async def test_without_a_valid_policy_nothing_else_is_guessed_at(tmp_path: Path) -> None:
    checks = await doctor.diagnose(Settings(policy=tmp_path / "missing.yaml", state_dir=tmp_path / "state"))
    found = _by_name(checks)
    assert found["policy"].failed and found["identity"].status == "skipped"
    assert all(
        found[name].status == "skipped" for name in ("records", "dns", "proxy", "tls", "door", "clock")
    )


def test_identity_fails_on_a_key_others_can_read(tmp_path: Path) -> None:
    directory = tmp_path / "identity"
    assert doctor.identity_check(directory).status == "skipped"
    own = identities.create(directory)
    ready = doctor.identity_check(directory)
    assert ready.status == "ok" and own.fingerprint in ready.detail and "not enrolled" in ready.detail
    (directory / "sealing.key").chmod(0o644)
    exposed = doctor.identity_check(directory)
    assert exposed.failed and "open to others" in exposed.detail


async def test_records_fails_when_the_audit_log_or_the_journal_cannot_be_written(tmp_path: Path) -> None:
    door = FakeDoor()
    a_file = tmp_path / "a-file"
    a_file.write_text("", encoding="utf-8")
    good = policies.load(write_policy(tmp_path, policy_document(door, tmp_path)))
    assert (await doctor.records_check(good, tmp_path / "state" / "journal")).status == "ok"
    document = policy_document(door, tmp_path) | {"audit": {"file": str(a_file / "audit.jsonl")}}
    unwritable = await doctor.records_check(
        policies.load(write_policy(tmp_path, document)), tmp_path / "journal"
    )
    assert unwritable.failed and "audit" in unwritable.detail and "cannot record" in unwritable.hint
    no_journal = await doctor.records_check(good, a_file / "journal")
    assert no_journal.failed and "journal cannot be written" in no_journal.detail


async def test_dns_fails_when_the_doors_name_does_not_resolve() -> None:
    async def nowhere(host: str, _port: int) -> list[str]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    failed = await doctor.dns_check("https://outposts.example.com", None, nowhere)
    assert failed.failed and "outposts.example.com (the door) does not resolve" in failed.detail
    assert "Name or service not known" in failed.detail
    assert (await doctor.dns_check("https://outposts.example.com", None, _resolves)).status == "ok"
    # Behind an outbound proxy the daemon resolves its proxy, and the proxy resolves the door.
    behind = await doctor.dns_check("https://outposts.example.com", "http://proxy.corp.example:3128", nowhere)
    assert behind.failed and "proxy.corp.example (the outbound proxy)" in behind.detail
    assert (await doctor.dns_check("https://localhost:8443", None)).status == "ok", "the real resolver"
    assert (await doctor.dns_check("http://127.0.0.1:8080", None)).status == "skipped"


async def test_tls_fails_behind_a_private_authority_this_machine_does_not_trust(tmp_path: Path) -> None:
    ca_file, tls = private_authority(tmp_path / "pki")
    door = FakeDoor()
    async with served(door.handle, tls=tls) as origin:
        unknown = await doctor.tls_check(origin, None, ssl.create_default_context())
        trusted = await doctor.tls_check(origin, None, Settings(ca_file=ca_file).trust())
    assert unknown.failed and "does not verify" in unknown.detail
    # It says who signed what was presented: the authority of whoever sits on the path.
    assert AUTHORITY in unknown.detail and "--ca-file" in unknown.hint
    assert trusted.status == "ok" and AUTHORITY in trusted.detail
    assert (
        await doctor.tls_check("http://127.0.0.1:1", None, ssl.create_default_context())
    ).status == "skipped"
    refused = await doctor.tls_check(f"https://127.0.0.1:{closed_port()}", None, ssl.create_default_context())
    assert refused.failed and "no TLS session" in refused.detail


async def test_tls_is_checked_through_the_outbound_proxy_when_there_is_one(tmp_path: Path) -> None:
    ca_file, tls = private_authority(tmp_path / "pki")
    door = FakeDoor()
    async with served(door.handle, tls=tls) as origin, proxy() as (proxy_url, asked):
        port = origin.rsplit(":", 1)[1]
        through = await doctor.tls_check(origin, proxy_url, Settings(ca_file=ca_file).trust())
        blind = await doctor.tls_check(origin, proxy_url, ssl.create_default_context())
    assert through.status == "ok" and asked[0] == f"CONNECT localhost:{port}"
    assert blind.failed and AUTHORITY in blind.detail


async def test_proxy_fails_when_the_outbound_proxy_refuses_the_tunnel_or_is_not_there() -> None:
    assert (await doctor.proxy_check("https://outposts.example.com", None)).status == "skipped"
    async with proxy("403 Forbidden") as (refusing, asked):
        refused = await doctor.proxy_check("https://outposts.example.com", refusing)
    assert refused.failed and "refused a tunnel to outposts.example.com:443" in refused.detail
    assert "403 Forbidden" in refused.detail and asked == ["CONNECT outposts.example.com:443"]
    async with proxy("407 Proxy Authentication Required") as (asking, _):
        unauthenticated = await doctor.proxy_check("https://outposts.example.com", asking)
        # Its credentials are never shown.
        with_password = asking.replace("http://", "http://outpost:proxy-password@")
        shown = await doctor.proxy_check("https://outposts.example.com", with_password)
    assert unauthenticated.failed and "credentials" in unauthenticated.hint
    assert "proxy-password" not in shown.detail + shown.hint and "outpost:" not in shown.detail
    gone = await doctor.proxy_check("https://outposts.example.com", f"http://127.0.0.1:{closed_port()}")
    assert gone.failed and "cannot be reached" in gone.detail
    door = FakeDoor()
    async with served(door.handle) as origin, proxy() as (working, _):
        opened = await doctor.proxy_check(origin, working)
    assert opened.status == "ok"


async def _probe(handler: Any, now: float | None = None) -> tuple[doctor.Check, float | None]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await doctor.door_check(
            "https://outposts.example.com", http, (lambda: now) if now else time.time
        )


async def test_door_fails_when_what_answers_is_not_the_door() -> None:
    door = FakeDoor()
    reached, drift = await _probe(door.handle)
    assert reached.status == "ok" and "401 invalid_signature" in reached.detail and drift is not None

    absent, drift = await _probe(lambda _: httpx.Response(404, json={"detail": "Not Found"}))
    assert absent.failed and drift is None and "HTTP 404" in absent.detail and "switched off" in absent.hint
    portal, _ = await _probe(lambda _: httpx.Response(200, text="<html>Sign in to continue</html>"))
    assert portal.failed and "not one of the door's problems" in portal.detail

    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    unreachable, _ = await _probe(down)
    assert (
        unreachable.failed
        and "cannot be reached" in unreachable.detail
        and "ConnectError" in unreachable.detail
    )


async def test_clock_fails_when_this_machine_is_further_from_the_door_than_the_door_takes() -> None:
    door = FakeDoor()
    _, drift = await _probe(door.handle)
    assert doctor.clock_check(drift).status == "ok"
    door.skew(300)
    _, behind = await _probe(door.handle)
    late = doctor.clock_check(behind)
    assert late.failed and "behind the door's" in late.detail and "stale_iat" in late.hint
    door.skew(-300)
    _, ahead = await _probe(door.handle)
    early = doctor.clock_check(ahead)
    assert early.failed and "ahead of the door's" in early.detail
    door.skew(45)
    _, near = await _probe(door.handle)
    assert doctor.clock_check(near).status == "ok", "the door takes 60 seconds either way"
    assert doctor.clock_check(None).status == "skipped"


async def test_an_endpoint_fails_when_its_server_does_not_answer_with_its_credential(tmp_path: Path) -> None:
    door = FakeDoor()
    policy = policies.load(write_policy(tmp_path, policy_document(door, tmp_path)))
    server = FakeMcpServer(tools=TOOLS, token=CREDENTIAL)
    good = await doctor.endpoint_check("tracker", policy, ToolHost(policy, transport=server.transport))
    assert good.status == "ok" and good.name == "endpoint tracker"

    server.token = "a-credential-the-outpost-does-not-hold"
    refused = await doctor.endpoint_check("tracker", policy, ToolHost(policy, transport=server.transport))
    assert refused.failed and "refused by the server (401)" in refused.detail and "auth" in refused.hint

    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    unreachable = await doctor.endpoint_check(
        "tracker", policy, ToolHost(policy, transport=lambda: httpx.MockTransport(down))
    )
    assert unreachable.failed and "does not answer a tool listing" in unreachable.detail

    (tmp_path / "tracker-token").unlink()
    no_credential = await doctor.endpoint_check(
        "tracker", policy, ToolHost(policy, transport=server.transport)
    )
    assert no_credential.failed and "credential file" in no_credential.detail


async def test_each_alias_has_its_own_check(tmp_path: Path, daemon_log: io.StringIO) -> None:
    door = FakeDoor()
    document = policy_document(door, tmp_path)
    document["endpoints"]["forge"] = {"url": "http://forge-mcp.tools.svc:8080/mcp", "tools": ["*"]}
    path = write_policy(tmp_path, document)

    def one_of_two() -> httpx.MockTransport:
        server = FakeMcpServer(tools=TOOLS, token=CREDENTIAL)

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.host.startswith("forge"):
                raise httpx.ConnectError("connection refused")
            return server.respond(request)

        return httpx.MockTransport(respond)

    settings = Settings(policy=path, state_dir=tmp_path / "state", proxy=f"http://127.0.0.1:{closed_port()}")
    checks = await doctor.diagnose(settings, door=door.transport(), endpoints=one_of_two, resolve=_resolves)
    found = _by_name(checks)
    assert found["endpoint forge"].failed and found["endpoint tracker"].status == "ok"
    assert found["proxy"].failed, "the proxy the operator named is not there"
    text = doctor.render(checks)
    assert "FAILED  endpoint forge" in text and "-> check its `url` and its `auth`" in text
    assert json.loads(doctor.as_json(checks))["ok"] is False
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["door_keys"] == [door.public_key]

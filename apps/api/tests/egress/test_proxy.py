"""The per-run egress proxy (ADR 0070), on real sockets: what it lets through, what it refuses and says."""

from __future__ import annotations

import asyncio

import pytest
from choregos_api.egress.config import EgressSettings
from choregos_api.egress.proxy import EgressProxy, is_public
from choregos_api.egress.report import FakeDecisionSink, Reporter
from choregos_api.egress.sources import FakeRunNetworkSource

from .helpers import LOOPBACK, basic, connect_request, headers_of, rig

TOKEN = "run-token-for-tests"  # not a credential: it is only compared with itself by a fake


def _auth(run_id: str = "run-1") -> str:
    return basic(run_id, TOKEN)


async def _closed(writer: asyncio.StreamWriter) -> None:
    writer.close()
    await asyncio.sleep(0.05)


async def test_without_credentials_the_proxy_answers_407() -> None:
    async with rig() as r:
        _, writer, status, blob = await connect_request(r.port, "allowed.test:443")
        assert status.startswith("HTTP/1.1 407")
        assert headers_of(blob)["proxy-authenticate"] == 'Basic realm="choregos-egress"'
        assert b"credentials are required" in blob
        await _closed(writer)
        assert r.source.calls == 0, "nothing is asked of the API without credentials"


async def test_credentials_the_api_does_not_know_are_a_407_and_an_unreachable_api_fails_closed() -> None:
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["allowed.test"])
        _, writer, status, _ = await connect_request(r.port, "allowed.test:443", auth=basic("run-1", "wrong"))
        assert status.startswith("HTTP/1.1 407")
        await _closed(writer)
        r.source.unavailable = True
        _, writer, status, body = await connect_request(r.port, "allowed.test:443", auth=_auth())
        assert status.startswith("HTTP/1.1 503") and b"try again" in body
        await _closed(writer)


async def test_an_allowed_host_gets_a_tunnel_that_carries_bytes_both_ways() -> None:
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["allowed.test"])
        reader, writer, status, _ = await connect_request(
            r.port, f"allowed.test:{r.upstream.port}", auth=_auth()
        )
        assert status.startswith("HTTP/1.1 200")
        writer.write(b"ping through the tunnel")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(23), 5) == b"ping through the tunnel"
        await _closed(writer)
        await asyncio.sleep(0.1)
        await r.reporter.flush()
        rows = {(d["host"], d["decision"]): d for _, batch in r.sink.posts for d in batch}
        row = rows[("allowed.test", "allowed")]
        assert row["layer"] == "proxy" and row["reason"] == "allowed:allowed.test"
        assert row["count"] == 1 and row["bytes_up"] == 23 and row["bytes_down"] == 23


async def test_a_subdomain_of_an_allowed_host_and_the_implied_host_pass() -> None:
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["allowed.test"], implied=["repo.test"])
        for host in ("deep.allowed.test", "repo.test"):
            _, writer, status, _ = await connect_request(r.port, f"{host}:{r.upstream.port}", auth=_auth())
            assert status.startswith("HTTP/1.1 200"), host
            await _closed(writer)


async def test_a_host_outside_the_list_is_a_403_that_names_the_host_the_reason_and_where_to_change_it() -> (
    None
):
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["allowed.test"])
        _, writer, status, blob = await connect_request(r.port, f"other.test:{r.upstream.port}", auth=_auth())
        assert status.startswith("HTTP/1.1 403")
        text = blob.decode()
        assert "other.test" in text and "not_allowed" in text and "Network section" in text
        await _closed(writer)


async def test_the_deny_list_wins_over_the_allow_list_and_so_does_the_deployments() -> None:
    async with rig(deny_domains=["instance.test"]) as r:
        r.source.put(
            TOKEN, run_id="run-1", allow=["allowed.test", "instance.test"], deny=["bad.allowed.test"]
        )
        _, writer, status, blob = await connect_request(
            r.port, f"bad.allowed.test:{r.upstream.port}", auth=_auth()
        )
        assert status.startswith("HTTP/1.1 403") and b"denied:bad.allowed.test" in blob
        await _closed(writer)
        _, writer, status, blob = await connect_request(
            r.port, f"instance.test:{r.upstream.port}", auth=_auth()
        )
        assert status.startswith("HTTP/1.1 403") and b"denied:instance.test" in blob
        await _closed(writer)


async def test_an_ip_literal_is_refused_even_when_the_list_is_a_star() -> None:
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["*"])
        for target in (f"127.0.0.1:{r.upstream.port}", f"[::1]:{r.upstream.port}"):
            _, writer, status, blob = await connect_request(r.port, target, auth=_auth())
            assert status.startswith("HTTP/1.1 403") and b"ip_literal" in blob, target
            await _closed(writer)


async def test_a_port_that_is_not_open_is_refused_whatever_the_host() -> None:
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["allowed.test"])
        _, writer, status, blob = await connect_request(r.port, "allowed.test:2222", auth=_auth())
        assert status.startswith("HTTP/1.1 403") and b"port_not_allowed" in blob
        await _closed(writer)


async def test_a_name_that_resolves_to_a_private_address_is_refused() -> None:
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["*"])
        _, writer, status, blob = await connect_request(
            r.port, f"private.test2:{r.upstream.port}", auth=_auth()
        )
        assert status.startswith("HTTP/1.1 403") and b"private_address" in blob
        await _closed(writer)


async def test_the_rate_per_host_answers_429_with_retry_after_and_recovers() -> None:
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["allowed.test"], per_host_per_minute=2)
        target = f"allowed.test:{r.upstream.port}"
        for _ in range(2):
            _, writer, status, _ = await connect_request(r.port, target, auth=_auth())
            assert status.startswith("HTTP/1.1 200")
            await _closed(writer)
        _, writer, status, blob = await connect_request(r.port, target, auth=_auth())
        assert status.startswith("HTTP/1.1 429")
        assert int(headers_of(blob)["retry-after"]) >= 1
        await _closed(writer)
        r.clock.now += 61
        _, writer, status, _ = await connect_request(r.port, target, auth=_auth())
        assert status.startswith("HTTP/1.1 200")
        await _closed(writer)
        await r.reporter.flush()
        decisions = {d["decision"] for _, batch in r.sink.posts for d in batch}
        assert "rate_limited" in decisions


async def test_the_rate_per_run_and_the_organisations_rate_per_host_hold_too() -> None:
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["a.test", "b.test"], per_run_per_minute=2)
        for host, expected in (("a.test", "200"), ("b.test", "200"), ("a.test", "429")):
            _, writer, status, _ = await connect_request(r.port, f"{host}:{r.upstream.port}", auth=_auth())
            assert status.startswith(f"HTTP/1.1 {expected}"), host
            await _closed(writer)
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["a.test"], org_per_host_per_minute=1)
        r.source.put("other-token", run_id="run-2", allow=["a.test"], org_per_host_per_minute=1)
        _, w1, s1, _ = await connect_request(r.port, f"a.test:{r.upstream.port}", auth=_auth("run-1"))
        _, w2, s2, _ = await connect_request(
            r.port, f"a.test:{r.upstream.port}", auth=basic("run-2", "other-token")
        )
        assert s1.startswith("HTTP/1.1 200") and s2.startswith("HTTP/1.1 429"), (
            "two runs of one organisation share it"
        )
        await _closed(w1)
        await _closed(w2)


async def test_a_run_cannot_hold_more_tunnels_than_the_cap() -> None:
    async with rig(max_tunnels_per_run=1) as r:
        r.source.put(TOKEN, run_id="run-1", allow=["allowed.test"])
        target = f"allowed.test:{r.upstream.port}"
        _, first, status, _ = await connect_request(r.port, target, auth=_auth())
        assert status.startswith("HTTP/1.1 200")
        _, second, status, blob = await connect_request(r.port, target, auth=_auth())
        assert status.startswith("HTTP/1.1 429") and b"too_many_tunnels" in blob
        await _closed(second)
        await _closed(first)
        await asyncio.sleep(0.1)
        _, third, status, _ = await connect_request(r.port, target, auth=_auth())
        assert status.startswith("HTTP/1.1 200"), "the slot is given back when the tunnel closes"
        await _closed(third)


async def test_an_allowed_host_that_cannot_be_reached_is_a_502_unreachable() -> None:
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["allowed.test", "gone.invalid"])
        dead = await asyncio.start_server(lambda *_: None, LOOPBACK, 0)
        port = dead.sockets[0].getsockname()[1]
        dead.close()
        await dead.wait_closed()
        r.proxy.settings.ports.append(port)
        _, writer, status, blob = await connect_request(r.port, f"allowed.test:{port}", auth=_auth())
        assert status.startswith("HTTP/1.1 502") and b"could not be reached" in blob
        await _closed(writer)
        _, writer, status, _ = await connect_request(r.port, f"gone.invalid:{r.upstream.port}", auth=_auth())
        assert status.startswith("HTTP/1.1 502"), "an unresolvable name"
        await _closed(writer)
        await r.reporter.flush()
        assert any(d["decision"] == "unreachable" for _, batch in r.sink.posts for d in batch)


async def test_plain_http_is_forwarded_in_origin_form_without_the_proxy_credentials_or_added_headers() -> (
    None
):
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["allowed.test"])
        reader, writer = await asyncio.open_connection(LOOPBACK, r.port)
        writer.write(
            (
                f"GET http://allowed.test:{r.upstream.port}/path?q=1 HTTP/1.1\r\nHost: allowed.test\r\n"
                f"Proxy-Authorization: {_auth()}\r\nProxy-Connection: keep-alive\r\nUser-Agent: unit\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(65536), 5)
        assert response.startswith(b"HTTP/1.1 200") and response.endswith(b"hello")
        sent = r.upstream.requests[0]
        assert sent.startswith(b"GET /path?q=1 HTTP/1.1\r\n")
        lowered = sent.lower()
        for forbidden in (b"proxy-authorization", b"proxy-connection", b"x-forwarded-for", b"via:"):
            assert forbidden not in lowered, forbidden
        assert b"user-agent: unit" in lowered and b"connection: close" in lowered
        await _closed(writer)


async def test_plain_http_to_a_host_outside_the_list_is_refused_like_a_connect() -> None:
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["allowed.test"])
        reader, writer = await asyncio.open_connection(LOOPBACK, r.port)
        writer.write(
            (
                f"GET http://evil.test:{r.upstream.port}/ HTTP/1.1\r\nProxy-Authorization: {_auth()}\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        assert (await asyncio.wait_for(reader.read(65536), 5)).startswith(b"HTTP/1.1 403")
        await _closed(writer)


async def test_a_request_that_is_not_for_a_proxy_or_is_malformed_is_a_400() -> None:
    async with rig() as r:
        for raw in (
            b"GET /healthz HTTP/1.1\r\nProxy-Authorization: " + _auth().encode() + b"\r\n\r\n",
            b"nonsense\r\n\r\n",
        ):
            reader, writer = await asyncio.open_connection(LOOPBACK, r.port)
            writer.write(raw)
            await writer.drain()
            assert (await asyncio.wait_for(reader.read(65536), 5)).startswith(b"HTTP/1.1 400")
            await _closed(writer)
        reader, writer = await asyncio.open_connection(LOOPBACK, r.port)
        writer.write(b"GET http://x.test/ HTTP/1.1\r\n" + b"X-Pad: " + b"a" * 20000 + b"\r\n\r\n")
        await writer.drain()
        assert (await asyncio.wait_for(reader.read(65536), 5)).startswith(b"HTTP/1.1 431")
        await _closed(writer)


async def test_the_default_predicate_refuses_the_loopback_without_any_injection() -> None:
    """No `public=` injected, as in production: a name that resolves to 127.0.0.1 is never reachable."""
    from .helpers import upstream_server

    async with upstream_server() as upstream:
        source = FakeRunNetworkSource()
        source.put(TOKEN, run_id="run-1", allow=["allowed.test"])

        async def resolver(host: str, port: int) -> list[str]:
            return [LOOPBACK]

        proxy = EgressProxy(
            EgressSettings(ports=[upstream.port]), source, Reporter(FakeDecisionSink()), resolver=resolver
        )
        port = await proxy.start("127.0.0.1", 0)
        try:
            _, writer, status, blob = await connect_request(
                port, f"allowed.test:{upstream.port}", auth=_auth()
            )
            assert status.startswith("HTTP/1.1 403") and b"private_address" in blob
            await _closed(writer)
        finally:
            await proxy.close()


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("127.0.0.1", False), ("::1", False), ("10.1.2.3", False), ("172.16.0.1", False),
        ("192.168.1.1", False), ("169.254.169.254", False), ("100.64.0.1", False), ("0.0.0.0", False),
        ("224.0.0.1", False), ("::ffff:10.0.0.1", False), ("fe80::1", False), ("fc00::1", False),
        ("93.184.216.34", True), ("2606:2800:220:1:248:1893:25c8:1946", True), ("not an address", False),
    ],
)  # fmt: skip
def test_is_public(address: str, public: bool) -> None:
    assert is_public(address) is public


async def test_the_reporter_posts_cumulative_totals_that_a_retry_rewrites_and_survives_a_failing_api() -> (
    None
):
    sink = FakeDecisionSink()
    reporter = Reporter(sink)
    reporter.record("run-1", TOKEN, "a.test", 443, "allowed", "allowed:a.test", up=5, down=7)
    reporter.record("run-1", TOKEN, "a.test", 443, "allowed", "allowed:a.test", up=1, down=1)
    sink.fail = True
    assert await reporter.flush() == 0, "a refused post is not fatal"
    sink.fail = False
    assert await reporter.flush() == 1
    assert await reporter.flush() == 0, "nothing changed since"
    reporter.record("run-1", TOKEN, "a.test", 443, "allowed", "allowed:a.test")
    await reporter.flush()
    first, second = (batch[0] for _, batch in sink.posts)
    assert (first["count"], first["bytes_up"], first["bytes_down"]) == (2, 6, 8)
    assert second["count"] == 3, "totals, not increments: the API replaces"


async def test_closing_the_proxy_ends_the_tunnels_that_are_still_open() -> None:
    """A shutdown (SIGTERM) must not wait for a tunnel an agent keeps open."""
    async with rig() as r:
        r.source.put(TOKEN, run_id="run-1", allow=["allowed.test"])
        reader, writer, status, _ = await connect_request(
            r.port, f"allowed.test:{r.upstream.port}", auth=_auth()
        )
        assert status.startswith("HTTP/1.1 200")
        await asyncio.wait_for(r.proxy.close(), 5)
        assert await asyncio.wait_for(reader.read(10), 5) == b"", "the client sees the tunnel end"
        writer.close()

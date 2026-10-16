"""The egress proxy against the real API app and database (ADR 0070): token, cache, journal."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from choregos_api.egress.config import EgressSettings
from choregos_api.egress.proxy import EgressProxy
from choregos_api.egress.report import HttpDecisionSink, Reporter
from choregos_api.egress.sources import HttpRunNetworkSource, UnknownRunError
from httpx import ASGITransport, AsyncClient

from .helpers import LOOPBACK, Clock, _policy, _run, basic, connect_request, upstream_server

BASE = "http://test/api/v1"


class Counting(httpx.AsyncBaseTransport):
    """The ASGI transport, counting the requests the proxy's source really makes."""

    def __init__(self, app: Any) -> None:
        self.inner = ASGITransport(app=app)
        self.paths: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        return await self.inner.handle_async_request(request)


@pytest.fixture
async def edge(app: Any) -> AsyncIterator[tuple[Counting, AsyncClient]]:
    transport = Counting(app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield transport, http


def _token(headers: dict[str, str]) -> str:
    return headers["Authorization"].removeprefix("Bearer ")


async def _set_policy(client: AsyncClient, project: dict[str, Any], allow: list[str]) -> None:
    saved = await client.put(f"/api/v1/projects/{project['id']}/policy", json={"yaml": _policy(allow)})
    assert saved.status_code == 200, saved.text


async def test_a_runs_token_gives_the_proxy_its_effective_network_and_nobody_elses(
    client: AsyncClient, project: dict[str, Any], edge: tuple[Counting, AsyncClient]
) -> None:
    await _set_policy(client, project, ["pypi.org"])
    mine = await _run(project, "run-edge-1")
    other = await _run(project, "run-edge-2")
    _, http = edge
    source = HttpRunNetworkSource(BASE, client=http)

    network = await source.get("run-edge-1", _token(mine))
    assert network.allow == ["pypi.org"] and network.implied == ["github.com"]
    assert (network.org, network.project, network.run_id) == ("varga", "billing-api", "run-edge-1")

    with pytest.raises(UnknownRunError):
        await source.get("run-edge-1", _token(other))  # another run's token: 403
    with pytest.raises(UnknownRunError):
        await source.get("run-edge-1", "not-a-token")  # 403: an invalid token


async def test_the_answer_is_cached_for_cache_seconds_then_the_api_is_asked_again(
    client: AsyncClient, project: dict[str, Any], edge: tuple[Counting, AsyncClient]
) -> None:
    await _set_policy(client, project, ["pypi.org"])
    headers = await _run(project, "run-edge-3")
    transport, http = edge
    clock = Clock()
    source = HttpRunNetworkSource(BASE, client=http, cache_seconds=60, clock=clock)

    assert (await source.get("run-edge-3", _token(headers))).allow == ["pypi.org"]
    await _set_policy(client, project, ["pypi.org", "files.pythonhosted.org"])
    clock.now += 30
    assert (await source.get("run-edge-3", _token(headers))).allow == ["pypi.org"], "within the cache"
    assert len(transport.paths) == 1
    clock.now += 31
    assert (await source.get("run-edge-3", _token(headers))).allow == ["pypi.org", "files.pythonhosted.org"]
    assert len(transport.paths) == 2, "a changed policy applies once the cache has expired"


async def test_the_decisions_the_proxy_posts_land_in_the_journal_once_whatever_the_replays(
    client: AsyncClient, project: dict[str, Any], edge: tuple[Counting, AsyncClient]
) -> None:
    from choregos_api.db.models import NetworkDecision
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    headers = await _run(project, "run-edge-4", agent_slug="pip", agent_version=1)
    _, http = edge
    reporter = Reporter(HttpDecisionSink(BASE, client=http))
    token = _token(headers)
    reporter.record("run-edge-4", token, "pypi.org", 443, "allowed", "allowed:pypi.org", up=10, down=100)
    reporter.record("run-edge-4", token, "evil.example", 443, "denied", "not_allowed")
    assert await reporter.flush() == 1
    reporter.record("run-edge-4", token, "pypi.org", 443, "allowed", "allowed:pypi.org", up=5, down=50)
    assert await reporter.flush() == 1
    # A restarted proxy that replays the same totals changes nothing.
    again = Reporter(HttpDecisionSink(BASE, client=http))
    again.record(
        "run-edge-4", token, "pypi.org", 443, "allowed", "allowed:pypi.org", count=2, up=15, down=150
    )
    assert await again.flush() == 1

    async with session_scope() as session:
        rows = (await session.execute(select(NetworkDecision).order_by(NetworkDecision.host))).scalars().all()
    assert [(r.host, r.decision, r.layer, r.count, r.bytes_up, r.bytes_down) for r in rows] == [
        ("evil.example", "denied", "proxy", 1, 0, 0),
        ("pypi.org", "allowed", "proxy", 2, 15, 150),
    ]
    assert {r.agent_slug for r in rows} == {"pip"}


async def test_end_to_end_the_proxy_obeys_the_policy_stored_in_the_database(
    client: AsyncClient, project: dict[str, Any], edge: tuple[Counting, AsyncClient]
) -> None:
    from choregos_api.db.models import NetworkDecision
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _set_policy(client, project, ["pypi.org"])
    headers = await _run(project, "run-edge-5")
    token = _token(headers)
    _, http = edge
    async with upstream_server() as upstream:

        async def resolver(host: str, port: int) -> list[str]:
            return [LOOPBACK]

        reporter = Reporter(HttpDecisionSink(BASE, client=http))
        proxy = EgressProxy(
            EgressSettings(ports=[upstream.port]),
            HttpRunNetworkSource(BASE, client=http),
            reporter,
            resolver=resolver,
            public=lambda address: address == LOOPBACK,
        )
        port = await proxy.start("127.0.0.1", 0)
        try:
            auth = basic("run-edge-5", token)
            reader, writer, status, _ = await connect_request(port, f"pypi.org:{upstream.port}", auth=auth)
            assert status.startswith("HTTP/1.1 200")
            writer.write(b"x")
            assert await asyncio.wait_for(reader.readexactly(1), 5) == b"x"
            writer.close()
            _, refused, status, body = await connect_request(port, f"stripe.com:{upstream.port}", auth=auth)
            assert status.startswith("HTTP/1.1 403") and b"stripe.com" in body
            refused.close()
            _, repo, status, _ = await connect_request(port, f"github.com:{upstream.port}", auth=auth)
            assert status.startswith("HTTP/1.1 200"), "the repository host is implied"
            repo.close()
            _, bad, status, _ = await connect_request(
                port, f"pypi.org:{upstream.port}", auth=basic("run-edge-5", "no")
            )
            assert status.startswith("HTTP/1.1 407")
            bad.close()
            await asyncio.sleep(0.1)
            assert await reporter.flush() == 1
        finally:
            await proxy.close()
    async with session_scope() as session:
        rows = {
            (r.host, r.decision): r.count
            for r in (await session.execute(select(NetworkDecision))).scalars().all()
        }
    assert rows == {("pypi.org", "allowed"): 1, ("stripe.com", "denied"): 1, ("github.com", "allowed"): 1}

# SPDX-License-Identifier: Apache-2.0
"""The guarded transport refuses a host before sending (S39-22b, ADR 0066)."""

from __future__ import annotations

import httpx
import pytest
from choregos_adapters.errors import AdapterError
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.http.egress import GuardedTransport, guarded
from choregos_adapters.mcp import ClientMcp
from choregos_core.egress import AllowedRule, EgressPolicy, EgressRefusedError, use_policy

OUTILS = [{"name": "lire", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}]


class Recorder(httpx.AsyncBaseTransport):
    """What reached the inner transport."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        if request.url.host == "allowed.test" and request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "https://evil.test/landing"})
        return httpx.Response(200, text="ok")


def _policy(*hosts: str, mode: str = "enforce", ceiling: dict | None = None) -> EgressPolicy:
    return EgressPolicy(
        org="varga",
        rules=tuple(AllowedRule(h) for h in hosts),
        ceiling=ceiling or {},
        mode=mode,  # type: ignore[arg-type]
    )


async def _get(url: str, inner: Recorder, **kw: object) -> httpx.Response:
    async with httpx.AsyncClient(transport=guarded(inner), **kw) as client:  # type: ignore[arg-type]
        return await client.get(url)


async def test_an_absent_host_is_refused_before_the_inner_transport_sees_it() -> None:
    inner = Recorder()
    with use_policy(_policy("allowed.test")):
        assert (await _get("https://allowed.test/x", inner)).status_code == 200
        with pytest.raises(EgressRefusedError) as refused:
            await _get("https://evil.test/x", inner)
    assert inner.seen == ["https://allowed.test/x"], "the refused call never left"
    assert refused.value.reason == "org_absent" and "evil.test" in str(refused.value)


async def test_the_refusal_is_an_adapter_error_and_an_httpx_error() -> None:
    """Existing handlers (`except httpx.HTTPError`, `except AdapterError`) already catch it."""
    with use_policy(_policy()):
        with pytest.raises(httpx.HTTPError):
            await _get("https://evil.test/x", Recorder())
        with pytest.raises(AdapterError):
            await _get("https://evil.test/x", Recorder())


async def test_a_redirect_to_an_absent_host_is_refused_on_the_hop() -> None:
    inner = Recorder()
    with use_policy(_policy("allowed.test")), pytest.raises(EgressRefusedError) as refused:
        await _get("https://allowed.test/hop", inner, follow_redirects=True)
    assert inner.seen == ["https://allowed.test/hop"], "the first hop left, the second did not"
    assert refused.value.host == "evil.test"


async def test_the_ceiling_refuses_before_sending_with_the_platform_change_in_the_message() -> None:
    inner = Recorder()
    policy = _policy("mcp.test", ceiling={"api": ("api.github.com",)})
    with use_policy(policy), pytest.raises(EgressRefusedError) as refused:
        await _get("https://mcp.test/x", inner)
    assert refused.value.reason == "ceiling_outside" and inner.seen == []
    assert "platform" in str(refused.value)


async def test_a_port_and_a_family_must_match() -> None:
    inner = Recorder()
    with use_policy(_policy("allowed.test")), pytest.raises(EgressRefusedError):
        await _get("https://allowed.test:8443/x", inner)
    with use_policy(_policy("allowed.test"), family="workers"), pytest.raises(EgressRefusedError):
        await _get("https://allowed.test/x", inner)


async def test_without_a_policy_in_context_enforce_fails_closed_and_warn_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inner = Recorder()
    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    with pytest.raises(EgressRefusedError) as refused:
        await _get("https://allowed.test/x", inner)
    assert refused.value.reason == "no_policy_in_context" and inner.seen == []
    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "warn")
    assert (await _get("https://allowed.test/x", inner)).status_code == 200
    assert "egress.would_refuse" in capsys.readouterr().out, "warn logs what it would refuse"
    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "off")
    assert (await _get("https://allowed.test/y", inner)).status_code == 200


async def test_warn_mode_in_the_policy_logs_and_passes(capsys: pytest.CaptureFixture[str]) -> None:
    inner = Recorder()
    with use_policy(_policy(mode="warn")):
        assert (await _get("https://evil.test/x", inner)).status_code == 200
    assert "egress.would_refuse" in capsys.readouterr().out and inner.seen == ["https://evil.test/x"]


async def test_the_mcp_client_is_guarded() -> None:
    server = FakeMcpServer(outils=OUTILS)
    client = ClientMcp("https://vendor.test/mcp", transport=server.transport())
    with use_policy(_policy()), pytest.raises(EgressRefusedError):
        await client.list_tools()
    assert server.recues == [], "the vendor received nothing"
    with use_policy(_policy("vendor.test")):
        assert [o.name for o in await client.list_tools()] == ["lire"]


def test_guarded_wraps_the_real_transport_when_given_none() -> None:
    assert isinstance(guarded(None), GuardedTransport)

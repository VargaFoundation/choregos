"""The REST client shared by Jira and GitLab: bounded backoff, named errors, pagination."""

from __future__ import annotations

import httpx
import pytest
from choregos_adapters.errors import UpstreamError
from choregos_adapters.http import rest
from choregos_adapters.http.rest import RestClient

from ._transport import Wire, no_wait


async def test_a_429_is_replayed_honouring_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    waits = no_wait(monkeypatch, rest)
    wire = Wire(
        {
            ("GET", "/x"): [
                httpx.Response(429, headers={"Retry-After": "7"}, text="slow down"),
                (200, {"ok": True}),
            ]
        }
    )
    client = RestClient("https://api.test", client=wire.client(), service="jira")
    assert await client.request("GET", "/x") == {"ok": True}
    assert waits == [7.0], "the wait is the one the server asks for, not an exponential"
    assert len(wire.requests) == 2


async def test_the_backoff_is_bounded_and_ends_by_naming_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    waits = no_wait(monkeypatch, rest)
    wire = Wire({("GET", "/x"): (503, "maintenance")})
    client = RestClient("https://api.test", client=wire.client(), service="gitlab")
    with pytest.raises(UpstreamError) as exc:
        await client.request("GET", "/x")
    assert exc.value.service == "gitlab" and exc.value.status_code == 503
    assert "503 sur GET /x" in str(exc.value)
    assert len(waits) == rest.MAX_RETRIES and all(a <= 30.0 for a in waits)


async def test_a_client_error_is_not_replayed() -> None:
    wire = Wire({("POST", "/x"): (400, {"errors": ["summary manquant"]})})
    client = RestClient("https://api.test", client=wire.client())
    with pytest.raises(UpstreamError) as exc:
        await client.request("POST", "/x", json={})
    assert len(wire.requests) == 1, "a 400 will not change by retrying"
    assert "summary manquant" in str(exc.value)


async def test_a_204_returns_an_empty_dict_and_the_headers_add_up() -> None:
    wire = Wire({("DELETE", "/x"): (204, None)})
    client = RestClient("https://api.test", headers={"X-Atlassian-Token": "no-check"}, client=wire.client())
    assert await client.request("DELETE", "/x", headers={"X-Extra": "1"}) == {}
    sent_headers = wire.requests[0].headers
    assert sent_headers["X-Atlassian-Token"] == "no-check" and sent_headers["X-Extra"] == "1"
    assert sent_headers["Accept"] == "application/json"


async def test_pagination_stops_at_the_incomplete_page_and_at_the_ceiling() -> None:
    full_page = [{"id": i} for i in range(100)]
    wire = Wire({("GET", "/items"): [(200, full_page), (200, full_page[:3])]})
    client = RestClient("https://api.test", client=wire.client())
    everything = await client.paginate("/items", params={"state": "opened"})
    assert len(everything) == 103
    pages = [r.url.params["page"] for r in wire.requests]
    assert pages == ["1", "2"] and wire.requests[0].url.params["state"] == "opened"
    wire2 = Wire({("GET", "/items"): (200, full_page)})
    assert (
        len(await RestClient("https://api.test", client=wire2.client()).paginate("/items", limit=150)) == 150
    )

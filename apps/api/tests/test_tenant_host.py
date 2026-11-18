"""A host an organisation owns lands on that organisation, theme first (ADR 0175, S74-01)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_api import edition, extensions
from tranibor_api.tenant_host import declare_host_resolver, has_host_resolver, normalise_host, resolve_host

from .theme_fixtures import sample


@pytest.fixture(autouse=True)
def _clean_seams() -> Iterator[None]:
    extensions.reset()
    edition.reset()
    yield
    extensions.reset()
    edition.reset()


class Hosts:
    """A scriptable resolver and a theme provider that names the organisation it was asked for."""

    def __init__(self, owned: dict[str, Any]) -> None:
        self.owned = owned
        self.asked: list[str] = []
        self.themed: list[str | None] = []

    def resolve(self, session: Any, host: str) -> Any:
        self.asked.append(host)
        return self.owned.get(host)

    def theme(self, session: Any, org: str | None) -> dict[str, Any]:
        self.themed.append(org)
        return sample(id=org or "instance")


def _plug(owned: dict[str, Any]) -> Hosts:
    hosts = Hosts(owned)
    extensions.declare_theme_provider(hosts.theme)
    declare_host_resolver(hosts.resolve)
    return hosts


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("agentic.client.com", "agentic.client.com"),
        ("Agentic.Client.COM:8443", "agentic.client.com"),
        ("agentic.client.com.", "agentic.client.com"),
        ("agentic.client.com, proxy.internal", "agentic.client.com"),
        ("xn--caf-dma.example.com", "xn--caf-dma.example.com"),
        ("localhost", None),  # no dot: never a custom host
        ("10.1.2.3", None),
        ("[::1]:8000", None),
        ("a..b.com", None),
        ("-a.example.com", None),
        ("café.example.com", None),  # an IDN travels as its xn-- form
        ("a b.example.com", None),
        ("a.example.com/../x", None),
        (("a" * 64) + ".example.com", None),
        (".".join(["abcdefghi"] * 30), None),  # over 253 characters
        ("", None),
        (None, None),
    ],
)
def test_only_a_well_formed_dns_name_is_a_host(raw: str | None, expected: str | None) -> None:
    assert normalise_host(raw) == expected


async def test_without_a_resolver_every_host_is_the_instance(client: AsyncClient) -> None:
    assert not has_host_resolver()
    extensions.declare_theme_provider(lambda session, org: sample(id=org or "instance"))
    response = await client.get("/api/v1/theme", headers={"Host": "agentic.client.com"})
    assert response.json()["id"] == "instance"
    assert "x-choregos-host-org" not in response.headers


async def test_a_host_an_organisation_owns_serves_that_organisations_theme(client: AsyncClient) -> None:
    hosts = _plug({"agentic.client.com": "acme"})
    response = await client.get("/api/v1/theme", headers={"Host": "agentic.client.com"})
    assert response.status_code == 200
    assert response.json()["id"] == "acme"
    assert response.headers["x-choregos-host-org"] == "acme"
    assert response.headers["x-choregos-theme"] == "provided"
    assert hosts.themed == ["acme"]


async def test_the_forwarded_host_is_the_visitors_host_and_wins_over_host(client: AsyncClient) -> None:
    hosts = _plug({"agentic.client.com": "acme"})
    response = await client.get(
        "/api/v1/theme",
        headers={"Host": "web.choregos.svc", "X-Forwarded-Host": "Agentic.Client.com:443, edge.internal"},
    )
    assert response.json()["id"] == "acme"
    assert hosts.asked == ["agentic.client.com"]  # normalised: lower-case, no port, the first entry


async def test_the_hosts_organisation_prevails_over_the_remembered_one(client: AsyncClient) -> None:
    """The console sends the cookie's organisation too: on a tenant host it must not win."""
    hosts = _plug({"agentic.client.com": "acme"})
    response = await client.get(
        "/api/v1/theme", params={"org": "other"}, headers={"Host": "agentic.client.com"}
    )
    assert response.json()["id"] == "acme"
    assert hosts.themed == ["acme"]


async def test_an_unknown_host_keeps_the_requested_organisation_or_the_instance(client: AsyncClient) -> None:
    hosts = _plug({"agentic.client.com": "acme"})
    remembered = await client.get(
        "/api/v1/theme", params={"org": "other"}, headers={"Host": "console.example.com"}
    )
    assert remembered.json()["id"] == "other"
    assert "x-choregos-host-org" not in remembered.headers
    plain = await client.get("/api/v1/theme", headers={"Host": "console.example.com"})
    assert plain.json()["id"] == "instance"
    assert hosts.themed == ["other", None]


async def test_what_is_not_a_dns_name_never_reaches_the_resolver(client: AsyncClient) -> None:
    hosts = _plug({"10.0.0.1": "acme", "localhost": "acme"})
    for host in ("10.0.0.1", "localhost:3000", "a b.example.com"):
        assert (await client.get("/api/v1/theme", headers={"X-Forwarded-Host": host})).json()[
            "id"
        ] == "instance"
    assert hosts.asked == []


async def test_a_resolver_answer_that_is_not_a_slug_is_no_organisation(client: AsyncClient) -> None:
    hosts = _plug({"agentic.client.com": "../Admin"})
    response = await client.get("/api/v1/theme", headers={"Host": "agentic.client.com"})
    assert response.json()["id"] == "instance"
    assert "x-choregos-host-org" not in response.headers
    assert hosts.themed == [None]


async def test_a_resolver_that_fails_gives_the_instances_look_not_an_error(client: AsyncClient) -> None:
    extensions.declare_theme_provider(lambda session, org: sample(id=org or "instance"))

    def broken(session: Any, host: str) -> str:
        raise RuntimeError("the hosts table is gone")

    declare_host_resolver(broken)
    response = await client.get(
        "/api/v1/theme", params={"org": "other"}, headers={"Host": "agentic.client.com"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == "other"


async def test_an_async_resolver_is_awaited(client: AsyncClient) -> None:
    extensions.declare_theme_provider(lambda session, org: sample(id=org or "instance"))

    async def resolver(session: Any, host: str) -> str:
        return "acme"

    declare_host_resolver(resolver)
    response = await client.get("/api/v1/theme", headers={"Host": "agentic.client.com"})
    assert response.json()["id"] == "acme"


async def test_a_host_resolver_alone_still_names_the_organisation(client: AsyncClient) -> None:
    """No theme provider (community theme), but the sign-in page still needs the host's organisation."""
    declare_host_resolver(lambda session, host: "acme")
    response = await client.get("/api/v1/theme", headers={"Host": "agentic.client.com"})
    assert response.json()["id"] == "default"
    assert response.headers["x-choregos-host-org"] == "acme"


async def test_the_answer_varies_with_the_host(client: AsyncClient) -> None:
    response = await client.get("/api/v1/theme")
    assert {v.strip().lower() for v in response.headers["vary"].split(",")} >= {"host", "x-forwarded-host"}


def test_a_second_resolver_replaces_the_first_and_reset_clears_it() -> None:
    declare_host_resolver(lambda session, host: "first")
    declare_host_resolver(lambda session, host: "second")
    assert has_host_resolver()
    edition.reset()
    assert not has_host_resolver()


async def test_resolve_host_without_a_resolver_is_none() -> None:
    assert await resolve_host(None, "agentic.client.com") is None
    with pytest.raises(TypeError):
        declare_host_resolver("not callable")  # type: ignore[arg-type]

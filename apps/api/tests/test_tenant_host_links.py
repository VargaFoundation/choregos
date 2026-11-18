# SPDX-License-Identifier: Apache-2.0
"""A link built for an organisation uses its verified host (ADR 0175, S74-03 amendment).

`varga` owns `agentic.varga.example` (verified) and has claimed `claimed.varga.example` without proving it.
The extension answers the reverse question (`declare_org_host_resolver`); the core keeps its answer only if
the forward resolver gives the organisation back. The instance (`http://localhost:3000`) stays valid.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from choregos_api import tenant_host
from httpx import AsyncClient

from .test_a2a_inbound import CARD, _ready

HOST = "agentic.varga.example"
CLAIMED = "claimed.varga.example"
OTHER = "portal.globex.example"
INSTANCE = "http://localhost:3000"
ON = {"x-forwarded-host": HOST}


class Table:
    """The extension's table, seen from both sides."""

    def __init__(self) -> None:
        self.verified: dict[str, str] = {HOST: "varga", OTHER: "globex"}
        self.primary: Any = {"varga": HOST}
        self.asked: list[str] = []

    def forward(self, session: Any, host: str) -> str | None:
        return self.verified.get(host)

    def reverse(self, session: Any, slug: str) -> Any:
        self.asked.append(slug)
        found = self.primary.get(slug)
        if isinstance(found, Exception):
            raise found
        return found


@pytest.fixture
def table() -> Iterator[Table]:
    from choregos_api import edition

    found = Table()
    tenant_host.reset()
    tenant_host.declare_host_resolver(found.forward)
    tenant_host.declare_org_host_resolver(found.reverse)
    try:
        yield found
    finally:
        edition.reset()
        tenant_host.reset()


# ───────────────────────────── the seam ─────────────────────────────


async def test_without_a_reverse_resolver_every_link_is_the_instances(client: AsyncClient) -> None:
    tenant_host.reset()
    tenant_host.declare_host_resolver(lambda session, host: "varga")  # forward only: the CE-like default
    assert await tenant_host.org_host("varga") is None
    assert await tenant_host.org_base_url("varga") == INSTANCE


async def test_a_verified_primary_host_is_the_organisations_base_with_the_instances_scheme(
    client: AsyncClient, table: Table
) -> None:
    assert await tenant_host.org_host("varga") == HOST
    assert await tenant_host.org_base_url("varga") == f"http://{HOST}"
    assert await tenant_host.org_base_url("acme") == INSTANCE  # no host: the instance's, still valid
    assert await tenant_host.org_base_url(None) == INSTANCE


async def test_a_host_that_the_forward_side_does_not_verify_never_reaches_a_link(
    client: AsyncClient, table: Table
) -> None:
    table.primary = {"varga": CLAIMED}  # claimed, not proved: the forward resolver says nobody
    assert await tenant_host.org_base_url("varga") == INSTANCE


async def test_another_organisations_host_never_reaches_a_link(client: AsyncClient, table: Table) -> None:
    table.primary = {"varga": OTHER}  # verified, but for globex
    assert await tenant_host.org_base_url("varga") == INSTANCE


@pytest.mark.parametrize(
    "answer",
    [
        "https://agentic.varga.example",
        "Agentic.Varga.Example",
        "agentic.varga.example:8443",
        "10.0.0.1",
        "x",
        42,
        "",
    ],
)
async def test_an_answer_that_is_not_a_bare_dns_name_is_no_host(
    client: AsyncClient, table: Table, answer: Any
) -> None:
    table.verified.update({"agentic.varga.example": "varga"})
    table.primary = {"varga": answer}
    assert await tenant_host.org_base_url("varga") == INSTANCE


async def test_a_failing_reverse_resolver_gives_the_instances_link(client: AsyncClient, table: Table) -> None:
    table.primary = {"varga": RuntimeError("table unreachable")}
    assert await tenant_host.org_base_url("varga") == INSTANCE


async def test_the_answer_is_remembered_then_follows_a_withdrawn_claim(
    client: AsyncClient, table: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(tenant_host.time, "monotonic", lambda: clock[0])
    assert await tenant_host.org_host("varga") == HOST
    assert await tenant_host.org_host("varga") == HOST
    assert table.asked == ["varga"]  # the second link did not ask
    del table.verified[HOST]  # the claim is withdrawn
    assert await tenant_host.org_host("varga") == HOST  # within the delay
    clock[0] += tenant_host.CACHE_TTL_S + 1
    assert await tenant_host.org_host("varga") is None  # and the link is the instance's again
    assert table.asked == ["varga", "varga"]


async def test_the_memo_is_bounded(client: AsyncClient, table: Table) -> None:
    for number in range(tenant_host.CACHE_MAX_ENTRIES + 5):
        await tenant_host.org_host(f"org-{number}")
    assert len(tenant_host._ORG_CACHE) <= tenant_host.CACHE_MAX_ENTRIES


# ───────────────────────────── the A2A door ─────────────────────────────


async def test_the_agent_card_of_an_organisation_with_a_host_points_to_it(
    client: AsyncClient, project: dict[str, Any], table: Table
) -> None:
    token = await _ready(client, project)
    card = (await client.get(CARD, headers={"Authorization": f"Bearer {token}"})).json()
    assert card["url"] == f"http://{HOST}/a2a/orgs/varga"
    assert card["provider"]["url"] == f"http://{HOST}"


async def test_the_agent_card_stays_on_the_instance_without_a_verified_host(
    client: AsyncClient, project: dict[str, Any], table: Table
) -> None:
    token = await _ready(client, project)
    table.primary = {"varga": CLAIMED}
    card = (await client.get(CARD, headers={"Authorization": f"Bearer {token}"})).json()
    assert card["url"] == f"{INSTANCE}/a2a/orgs/varga"


# ───────────────────────────── the MCP door ─────────────────────────────


@pytest.fixture
def oauth(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", "https://idp.test/realms/platform")
    reset_settings_cache()
    yield
    reset_settings_cache()


async def test_the_resource_metadata_names_the_door_the_request_came_in_on(
    client: AsyncClient, table: Table, oauth: None
) -> None:
    on_host = (await client.get("/.well-known/oauth-protected-resource/mcp", headers=ON)).json()
    assert on_host["resource"] == f"http://{HOST}/mcp"
    assert on_host["authorization_servers"] == ["https://idp.test/realms/platform"]  # the instance's IdP
    plain = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert plain["resource"] == f"{INSTANCE}/mcp"


async def test_a_spoofed_or_unverified_host_does_not_move_the_resource(
    client: AsyncClient, table: Table, oauth: None
) -> None:
    for host in (CLAIMED, "evil.example"):
        found = (
            await client.get("/.well-known/oauth-protected-resource/mcp", headers={"x-forwarded-host": host})
        ).json()
        assert found["resource"] == f"{INSTANCE}/mcp"


async def test_the_401_points_to_the_metadata_of_the_host_called(
    client: AsyncClient, table: Table, oauth: None
) -> None:
    refused = await client.post("/mcp", json={}, headers=ON)
    assert refused.status_code == 401
    assert (
        f'resource_metadata="http://{HOST}/.well-known/oauth-protected-resource/mcp"'
        in (refused.headers["www-authenticate"])
    )
    plain = await client.post("/mcp", json={})
    assert f'resource_metadata="{INSTANCE}/.well-known' in plain.headers["www-authenticate"]


async def test_the_integrations_page_shows_the_address_typed(
    client: AsyncClient, admin: str, table: Table
) -> None:
    on_host = (await client.get("/api/v1/integrations", headers=ON)).json()
    assert on_host["mcp_url"] == f"http://{HOST}/mcp"
    plain = (await client.get("/api/v1/integrations")).json()
    assert plain["mcp_url"] == f"{INSTANCE}/mcp"


# ───────────────────────────── the device sign-in ─────────────────────────────


async def test_the_device_page_is_on_the_organisations_host(
    client: AsyncClient, org: str, table: Table
) -> None:
    body = {"client_kind": "cli", "org": "varga"}
    started = (await client.post("/api/v1/auth/device", json=body)).json()
    assert started["verification_uri"] == f"http://{HOST}/device"
    assert started["verification_uri_complete"].startswith(f"http://{HOST}/device?user_code=")
    without = (await client.post("/api/v1/auth/device", json={"client_kind": "cli"})).json()
    assert without["verification_uri"] == f"{INSTANCE}/device"
    table.primary = {"varga": CLAIMED}
    tenant_host._ORG_CACHE.clear()  # the memo of the previous answer
    unverified = (await client.post("/api/v1/auth/device", json=body)).json()
    assert unverified["verification_uri"] == f"{INSTANCE}/device"


# ───────────────────────────── the console links of the services ─────────────────────────────


async def test_a_conversation_and_a_pending_link_use_the_organisations_base(
    client: AsyncClient, table: Table
) -> None:
    from choregos_api.services.conversation_actions import confirm_url
    from choregos_api.services.my_pending import _console

    assert confirm_url("c1", "a1", f"http://{HOST}") == f"http://{HOST}/conversations/c1?action=a1"
    assert confirm_url("c1", "a1") == f"{INSTANCE}/conversations/c1?action=a1"
    assert await _console("varga", "/inbox") == f"http://{HOST}/inbox"
    assert await _console("acme", "/inbox") == f"{INSTANCE}/inbox"

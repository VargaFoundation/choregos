"""The MCP catalogue: browse it, install an entry as a governed connector (ADR 0065, S39-10c).

An entry is an offer. Installing declares an ordinary `mcp` connector through the one door
connectors already use: the administrator only, the secret by reference, the tools born closed on
the first discovery.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator
from typing import Any

import pytest
import yaml
from choregos_core import mcp_catalogue
from httpx import AsyncClient

from .conftest import login

BASE = "/api/v1/orgs/varga/mcp-catalogue"
TOOLS: list[dict[str, Any]] = [
    {"name": "search", "description": "search", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "create_issue", "description": "write", "inputSchema": {"type": "object"}},
]  # fmt: skip


@pytest.fixture(autouse=True)
def _fresh_catalogue_cache() -> Iterator[None]:
    mcp_catalogue.load_with_site.cache_clear()
    yield
    mcp_catalogue.load_with_site.cache_clear()


@pytest.fixture
def vendor(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The server behind the catalogue's entries, faked: it demands the connector's key."""
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(outils=[dict(t) for t in TOOLS], jeton="vendor-key")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_CATALOGUE_TEST", "vendor-key")
    return server


def _by_id(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in body["entries"]}


async def test_a_member_lists_the_catalogue_and_nothing_is_installed(client: AsyncClient, admin: str) -> None:
    listed = await client.get(BASE)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    shipped = mcp_catalogue.load()
    assert body["meta"]["catalogue_version"] == shipped.meta.catalogue_version
    assert len(body["entries"]) == len(shipped.entries) >= 90
    assert {e["state"] for e in body["entries"]} == {"available"}
    assert all(e["installed_as"] == [] for e in body["entries"])
    assert (await client.get("/api/v1/orgs/varga/connectors")).json() == [], "listing enables nothing"
    github = _by_id(body)["github"]
    assert github["install"] == "installable" and github["official"] is True
    assert github["remote"]["url"] == "https://api.githubcopilot.com/mcp/"
    assert _by_id(body)["notion"]["install"] == "needs_oauth"
    assert _by_id(body)["playwright"]["install"] == "needs_hosting"


async def test_the_filters_narrow_the_list(client: AsyncClient, admin: str) -> None:
    everything = (await client.get(BASE)).json()["entries"]
    only_official = (await client.get(BASE, params={"official": "true"})).json()["entries"]
    assert 0 < len(only_official) < len(everything) and all(e["official"] for e in only_official)
    installable = (await client.get(BASE, params={"install": "installable"})).json()["entries"]
    assert installable and all(e["install"] == "installable" for e in installable)
    by_text = (await client.get(BASE, params={"q": "GITHUB"})).json()["entries"]
    assert "github" in {e["id"] for e in by_text} and len(by_text) < len(everything)
    by_category = (await client.get(BASE, params={"category": "databases"})).json()["entries"]
    assert by_category and all("databases" in e["categories"] for e in by_category)
    assert (await client.get(BASE, params={"install": "nonsense"})).status_code == 422


async def test_a_stranger_cannot_read_it(client: AsyncClient, admin: str) -> None:
    await client.get("/api/v1/auth/logout")
    client.cookies.clear()
    assert (await client.get(BASE)).status_code in {401, 403}


async def test_install_declares_a_closed_connector_with_a_reference(
    client: AsyncClient, admin: str, vendor: Any
) -> None:
    installed = await client.post(
        f"{BASE}/github/install", json={"secret_refs": {"token": "env:CONNECTOR_CATALOGUE_TEST"}}
    )
    assert installed.status_code == 201, installed.text
    connector = installed.json()
    version = mcp_catalogue.load().meta.catalogue_version
    assert connector["name"] == "github" and connector["type"] == "mcp" and connector["kind"] == "mcp"
    assert connector["config"] == {"url": "https://api.githubcopilot.com/mcp/"}
    assert connector["secret_refs"] == {"token": "env:CONNECTOR_CATALOGUE_TEST"}
    assert connector["catalogue_entry"] == f"github@{version}"
    assert connector["operations"] == [], "no operation is declared: they are discovered"

    discovered = await client.post("/api/v1/orgs/varga/connectors/github/discover")
    assert discovered.status_code == 200, discovered.text
    operations = (await client.get("/api/v1/orgs/varga/connectors/github")).json()["operations"]
    assert {o["name"]: o["policy"] for o in operations} == {
        "search": "forbidden",
        "create_issue": "forbidden",
    }
    for _, headers, _ in vendor.recues:
        assert headers["authorization"] == "Bearer vendor-key", "the connector's key, resolved in the API"

    state = _by_id((await client.get(BASE)).json())["github"]
    assert state["state"] == "installed" and state["installed_as"] == ["github"]


async def test_install_leaves_an_audit_row_naming_the_entry(client: AsyncClient, admin: str) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await client.post(f"{BASE}/exa/install", json={"name": "web-search"})
    async with session_scope() as session:
        rows = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "org.connector.create")))
            .scalars()
            .all()
        )
    (row,) = rows
    assert row.payload["name"] == "web-search"
    assert row.payload["catalogue"] == f"exa@{mcp_catalogue.load().meta.catalogue_version}"


async def test_only_the_administrator_installs(client: AsyncClient, admin: str) -> None:
    member = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "po@varga.dev", "role": "project_owner"}
    )
    assert member.status_code in {200, 201}
    await login(client, "po@varga.dev")
    assert (await client.get(BASE)).status_code == 200, "a member browses"
    refused = await client.post(f"{BASE}/exa/install", json={})
    assert refused.status_code == 403
    assert (await client.get("/api/v1/orgs/varga/connectors")).json() == []


async def test_an_oauth_entry_and_a_hosted_process_are_refused_with_the_reason(
    client: AsyncClient, admin: str
) -> None:
    oauth = await client.post(f"{BASE}/notion/install", json={})
    assert oauth.status_code == 422 and "OAuth" in oauth.text
    hosting = await client.post(f"{BASE}/playwright/install", json={})
    assert hosting.status_code == 422 and "local process" in hosting.text
    assert (await client.post(f"{BASE}/no-such-server/install", json={})).status_code == 404
    assert (await client.get("/api/v1/orgs/varga/connectors")).json() == []


async def test_a_taken_name_is_a_conflict_and_the_name_defaults_to_the_id(
    client: AsyncClient, admin: str
) -> None:
    assert (await client.post(f"{BASE}/exa/install", json={})).status_code == 201
    again = await client.post(f"{BASE}/exa/install", json={})
    assert again.status_code == 409
    other = await client.post(f"{BASE}/exa/install", json={"name": "exa-two"})
    assert other.status_code == 201
    entry = _by_id((await client.get(BASE)).json())["exa"]
    assert sorted(entry["installed_as"]) == ["exa", "exa-two"]


async def test_a_secret_in_clear_or_a_missing_one_is_refused(client: AsyncClient, admin: str) -> None:
    clear = await client.post(
        f"{BASE}/github/install", json={"secret_refs": {"token": "ghp_not_a_reference"}}
    )
    assert clear.status_code == 422, clear.text
    missing = await client.post(f"{BASE}/github/install", json={})
    assert missing.status_code == 422 and "secret_refs.token" in missing.text
    unwanted = await client.post(f"{BASE}/exa/install", json={"secret_refs": {"token": "env:CONNECTOR_X"}})
    assert unwanted.status_code == 422 and "no credential" in unwanted.text
    assert (await client.get("/api/v1/orgs/varga/connectors")).json() == []


async def test_a_credential_typed_at_install_is_sealed_and_never_returned(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sealed secrets (ADR 0058) and the catalogue meet: the value is typed once, a reference still works."""
    import base64
    import os

    from choregos_core.redaction import forget_secrets

    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", "k1:" + base64.b64encode(os.urandom(32)).decode())
    forget_secrets()
    typed = "vendor-key-" + "AAAA-1111"
    created = await client.post(f"{BASE}/github/install", json={"secret_values": {"token": typed}})
    assert created.status_code == 201, created.text
    assert typed not in created.text
    assert created.json()["catalogue_entry"].startswith("github@")
    assert [s["field"] for s in created.json()["secrets"]] == ["token"]
    both = await client.post(
        f"{BASE}/github/install",
        json={"name": "github-two", "secret_refs": {"token": "env:CONNECTOR_X"}},
    )
    assert both.status_code == 201, both.text


async def test_a_new_catalogue_version_shows_as_an_update(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.post(f"{BASE}/exa/install", json={})).status_code == 201
    shipped = mcp_catalogue.load()
    newer = shipped.model_copy(
        update={"meta": shipped.meta.model_copy(update={"catalogue_version": "2099.1.0"})}
    )
    monkeypatch.setattr(mcp_catalogue, "load", lambda: newer)
    mcp_catalogue.load_with_site.cache_clear()
    state = _by_id((await client.get(BASE)).json())["exa"]
    assert state["state"] == "update"


async def test_egress_is_declared_open_undeclared_or_unknown(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    unknown = _by_id((await client.get(BASE)).json())
    assert unknown["github"]["egress"] == "unknown", "the deployment declares nothing"

    monkeypatch.setenv("CHOREGOS_API_EGRESS_HOSTS", '["api.githubcopilot.com", "*.exa.ai"]')
    reset_settings_cache()
    declared = _by_id((await client.get(BASE)).json())
    assert declared["github"]["egress"] == "open"
    assert declared["exa"]["egress"] == "open", "a wildcard covers the subdomain"
    assert declared["context7"]["egress"] == "undeclared", "mcp.context7.com is not declared"
    assert declared["playwright"]["egress"] == "unknown", "a local process has no host to reach"


async def test_a_site_catalogue_adds_entries_after_the_shipped_one(
    client: AsyncClient, admin: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    site = tmp_path / "site.yaml"
    site.write_text(
        yaml.safe_dump(
            {"entries": [{
                "id": "insights", "title": "Insights", "description": "Our analytics MCP server.",
                "publisher": "Diametral", "official": False, "reference": False, "transport": "remote",
                "remote": {"url": "https://mcp.insights.internal.example/mcp", "protocol": "streamable-http"},
                "auth": "bearer", "egress_hosts": ["mcp.insights.internal.example"], "categories": ["data"],
            }]}
        )
    )  # fmt: skip
    monkeypatch.setenv("CHOREGOS_MCP_CATALOGUE_EXTRA", str(site))
    reset_settings_cache()
    body = (await client.get(BASE)).json()
    entry = _by_id(body)["insights"]
    assert entry["origin"] == "site" and entry["install"] == "installable"
    assert _by_id(body)["github"]["origin"] == "catalogue"
    assert body["meta"]["sources"]["site_catalogue"] == "site.yaml"
    assert [e["id"] for e in body["entries"]][-1] == "insights", "after the shipped entries"
    assert (
        await client.post(f"{BASE}/insights/install", json={"secret_refs": {"token": "env:CONNECTOR_X"}})
    ).status_code == 201


async def test_a_site_entry_cannot_shadow_a_shipped_one_nor_be_malformed(
    client: AsyncClient, admin: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    entry = {
        "id": "github", "title": "Fake", "description": "x", "publisher": "x", "official": True,
        "reference": False, "transport": "remote", "auth": "none", "categories": ["data"],
        "remote": {"url": "https://evil.example/mcp", "protocol": "streamable-http"},
    }  # fmt: skip
    site = tmp_path / "site.yaml"
    site.write_text(yaml.safe_dump({"entries": [entry]}))
    monkeypatch.setenv("CHOREGOS_MCP_CATALOGUE_EXTRA", str(site))
    reset_settings_cache()
    shadow = await client.get(BASE)
    assert shadow.status_code == 422 and "already in the shipped catalogue" in shadow.text

    site.write_text(
        yaml.safe_dump(
            {"entries": [{**entry, "id": "plain", "remote": {"url": "http://x.example", "protocol": "sse"}}]}
        )
    )
    mcp_catalogue.load_with_site.cache_clear()
    malformed = await client.get(BASE)
    assert malformed.status_code == 422 and "plain" in malformed.text


async def test_a_header_entry_installs_with_its_header_and_the_key_travels_in_it(
    client: AsyncClient, admin: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer
    from choregos_api.config import reset_settings_cache

    entry = {
        "id": "keyed", "title": "Keyed", "description": "A server that wants X-API-Key.", "publisher": "x",
        "official": False, "reference": False, "transport": "remote", "auth": "header:X-API-Key",
        "remote": {"url": "https://mcp.keyed.example/mcp", "protocol": "streamable-http"},
        "egress_hosts": ["mcp.keyed.example"], "categories": ["data"],
    }  # fmt: skip
    site = tmp_path / "site.yaml"
    site.write_text(yaml.safe_dump({"entries": [entry]}))
    monkeypatch.setenv("CHOREGOS_MCP_CATALOGUE_EXTRA", str(site))
    monkeypatch.setenv("CONNECTOR_CATALOGUE_KEYED", "the-raw-key")
    reset_settings_cache()
    server = FakeMcpServer(outils=[dict(t) for t in TOOLS])
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)

    installed = await client.post(
        f"{BASE}/keyed/install", json={"secret_refs": {"token": "env:CONNECTOR_CATALOGUE_KEYED"}}
    )
    assert installed.status_code == 201, installed.text
    assert installed.json()["config"] == {"url": "https://mcp.keyed.example/mcp", "auth_header": "X-API-Key"}
    assert (await client.post("/api/v1/orgs/varga/connectors/keyed/discover")).status_code == 200
    assert server.recues
    for method, headers, _ in server.recues:
        assert headers["x-api-key"] == "the-raw-key", method
        assert "authorization" not in headers, method


async def test_every_entry_names_the_setup_procedure_its_install_shows(
    client: AsyncClient, admin: str
) -> None:
    """Atlassian's entry has its own procedure; every other entry gets the generic MCP one (ADR 0067)."""
    entries = _by_id((await client.get(BASE)).json())
    assert entries["atlassian"]["procedure"] == "connector.mcp.mcp.atlassian-rovo"
    others = {e["procedure"] for i, e in entries.items() if i != "atlassian"}
    assert others == {"connector.mcp.mcp"}
    for procedure_id in {e["procedure"] for e in entries.values()}:
        assert (await client.get(f"/api/v1/procedures/{procedure_id}")).status_code == 200

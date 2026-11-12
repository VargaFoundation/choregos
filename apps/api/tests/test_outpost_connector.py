# SPDX-License-Identifier: Apache-2.0
"""Declaring a connector at an outpost (S65-13, ADR 0149 §1-2).

An `mcp-outpost` connector is `{outpost, endpoint, timeout_s}`: the name of an outpost of the same
organisation and the alias of an endpoint in that outpost's policy file. No URL and no secret: the
address and the credential are the outpost's and exist in its file only (ADR 0147 §7), so a
declaration that carries either is a 422. Declaring it routes the connector to the outpost
(`org_connectors.outpost_id`), which is then not deleted while the connector remains. It supports
the `service` identity and no other. Under `CHOREGOS_FAKES` and without a secrets key, its calls are
served by the in-memory `FakeOutpost`.
"""

from __future__ import annotations

from typing import Any

import choregos_adapters
import pytest
from httpx import AsyncClient

from .conftest import login
from .kits import outpost_daemon as daemon_kit
from .kits import outpost_route as route
from .kits import outposts as kit
from .kits.outpost_route import ALIAS, CONNECTOR, ORG, SLUG

CONNECTORS = f"{ORG}/connectors"


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    return route.switch_on(monkeypatch)


async def _declare(client: AsyncClient, config: dict[str, Any], **body: Any) -> Any:
    return await client.post(
        CONNECTORS, json={"name": CONNECTOR, "type": "mcp-outpost", "config": config} | body
    )


def _config(**changes: Any) -> dict[str, Any]:
    return {"outpost": SLUG, "endpoint": ALIAS} | changes


async def test_the_type_is_offered_with_the_contracts_schema_and_no_secret_field(
    client: AsyncClient, admin: str
) -> None:
    from choregos_contracts import load_openapi

    types = (await client.get("/api/v1/connectors/types")).json()
    (offered,) = [t for t in types if (t["kind"], t["type"]) == ("mcp", "mcp-outpost")]
    contract = dict(load_openapi()["components"]["schemas"]["McpOutpostConnectorConfig"])
    contract.pop("description")
    assert offered["config_schema"] == contract, "the declared schema is the contract's"
    assert offered["secret_fields"] == []
    assert offered["config_schema"]["additionalProperties"] is False


async def test_a_declaration_routes_the_connector_to_its_outpost(client: AsyncClient, admin: str) -> None:
    created = await kit.create(client, SLUG)
    declared = await _declare(client, _config(timeout_s=45))
    assert declared.status_code == 201, declared.text
    body = declared.json()
    assert (body["kind"], body["type"]) == ("mcp", "mcp-outpost")
    assert body["config"] == {"outpost": SLUG, "endpoint": ALIAS, "timeout_s": 45}
    assert not body["secrets"] and body["operations"] == [], "no secret, and its tools are discovered"
    row = await route.connector_row()
    assert row.outpost_id == (await kit.outpost_row(SLUG)).id
    assert created["connectors"] == []
    assert (await client.get(f"{ORG}/outposts/{SLUG}")).json()["connectors"] == [CONNECTOR]
    # The reference is what keeps the outpost: it is not deleted while a connector routes to it.
    assert (await client.post(f"{ORG}/outposts/{SLUG}/revoke")).status_code == 200
    kept = await client.delete(f"{ORG}/outposts/{SLUG}")
    assert kept.status_code == 409, kept.text
    assert (kept.json()["code"], kept.json()["connectors"]) == ("outpost_in_use", [CONNECTOR])
    assert (await client.delete(f"{CONNECTORS}/{CONNECTOR}")).status_code == 204
    assert (await client.delete(f"{ORG}/outposts/{SLUG}")).status_code == 204


@pytest.mark.parametrize(
    "config",
    [
        _config(url="https://tracker.internal.example.com/mcp"),
        _config(token="a credential"),
        _config(auth_header="X-Api-Key"),
        _config(server="tracker"),
        _config(identity_modes=["service", "user_exchange"]),
        _config(route="outpost"),
        {"outpost": SLUG},
        {"endpoint": ALIAS},
        _config(endpoint="https://tracker.internal.example.com/mcp"),
        _config(endpoint="Tracker"),
        _config(endpoint="tracker.tools.svc:8080"),
        _config(outpost="Factory Floor"),
        _config(timeout_s=0),
        _config(timeout_s=121),
        _config(timeout_s="soon"),
    ],
    ids=lambda config: ",".join(f"{k}={v}" for k, v in config.items())[:60],
)
async def test_a_declaration_with_an_address_a_secret_or_another_shape_is_a_422(
    client: AsyncClient, admin: str, config: dict[str, Any]
) -> None:
    await kit.create(client, SLUG)
    refused = await _declare(client, config)
    assert refused.status_code == 422, refused.text
    assert (await client.get(f"{CONNECTORS}/{CONNECTOR}")).status_code == 404, "nothing is declared"


@pytest.mark.parametrize(
    "secret",
    [{"secret_refs": {"token": "env:TRACKER_TOKEN"}}, {"secret_values": {"token": "a credential"}}],
    ids=["a reference", "a value"],
)
async def test_a_declaration_with_a_secret_is_a_422(
    client: AsyncClient, admin: str, secret: dict[str, Any]
) -> None:
    await kit.create(client, SLUG)
    refused = await _declare(client, _config(), **secret)
    assert refused.status_code == 422, refused.text
    assert "no secret field" in refused.json()["detail"]
    assert (await client.get(f"{CONNECTORS}/{CONNECTOR}")).status_code == 404


async def test_an_outpost_the_organisation_does_not_have_is_a_422(client: AsyncClient, admin: str) -> None:
    from choregos_api.db.models import Organization, Outpost
    from choregos_api.db.session import session_scope

    unknown = await _declare(client, _config())
    assert unknown.status_code == 422, unknown.text
    assert unknown.json()["errors"][0]["loc"] == ["body", "config", "outpost"]
    # Another organisation's outpost of that name is not this one's.
    async with session_scope(orgs="*") as session:
        other = Organization(slug="acme", name="Acme")
        session.add(other)
        await session.flush()
        session.add(
            Outpost(
                org_id=other.id, slug=SLUG, display_name="theirs", status="active", capabilities=["tools"]
            )
        )
    theirs = await _declare(client, _config())
    assert theirs.status_code == 422, theirs.text
    assert (await client.get(f"{CONNECTORS}/{CONNECTOR}")).status_code == 404


async def test_an_outpost_that_is_not_enabled_for_tools_serves_no_connector(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.db.models import Outpost
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    await kit.create(client, SLUG)
    async with session_scope(orgs="*") as session:
        await session.execute(update(Outpost).where(Outpost.slug == SLUG).values(capabilities=[]))
    refused = await _declare(client, _config())
    assert refused.status_code == 422 and "not enabled for tools" in refused.json()["detail"], refused.text


@pytest.mark.parametrize("gesture", ["pause", "revoke", "switch_off"])
async def test_an_outpost_that_is_handed_nothing_is_a_409_at_declaration(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch, gesture: str
) -> None:
    await kit.create(client, SLUG)
    if gesture == "switch_off":
        kit.configure(monkeypatch, outposts=None)
    else:
        assert (await client.post(f"{ORG}/outposts/{SLUG}/{gesture}")).status_code == 200
    refused = await _declare(client, _config())
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert (body["code"], body["outpost"], body["endpoint"]) == ("outpost_paused", SLUG, ALIAS)
    assert (await client.get(f"{CONNECTORS}/{CONNECTOR}")).status_code == 404, "nothing is declared"
    if gesture == "pause":
        assert (await client.post(f"{ORG}/outposts/{SLUG}/resume")).status_code == 200
        assert (await _declare(client, _config())).status_code == 201


async def test_an_edit_routes_the_connector_to_the_outpost_it_names(client: AsyncClient, admin: str) -> None:
    await kit.create(client, SLUG)
    await kit.create(client, "lab")
    assert (await _declare(client, _config())).status_code == 201
    moved = await client.patch(
        f"{CONNECTORS}/{CONNECTOR}", json={"config": {"outpost": "lab", "endpoint": "forge"}}
    )
    assert moved.status_code == 200, moved.text
    assert (await route.connector_row()).outpost_id == (await kit.outpost_row("lab")).id
    assert (await client.get(f"{ORG}/outposts/lab")).json()["connectors"] == [CONNECTOR]
    assert (await client.get(f"{ORG}/outposts/{SLUG}")).json()["connectors"] == []
    # Under the rules of its declaration: an unknown outpost, an address, a paused outpost.
    for config, status in (
        ({"outpost": "nowhere", "endpoint": "forge"}, 422),
        ({"outpost": "lab", "endpoint": "forge", "url": "https://forge.example.com"}, 422),
    ):
        refused = await client.patch(f"{CONNECTORS}/{CONNECTOR}", json={"config": config})
        assert refused.status_code == status, refused.text
    assert (await client.post(f"{ORG}/outposts/{SLUG}/pause")).status_code == 200
    back = await client.patch(f"{CONNECTORS}/{CONNECTOR}", json={"config": _config()})
    assert back.status_code == 409 and back.json()["code"] == "outpost_paused", back.text
    row = await route.connector_row()
    assert row.outpost_id == (await kit.outpost_row("lab")).id and row.config["outpost"] == "lab"


async def test_declaring_one_is_the_administrators(client: AsyncClient, admin: str) -> None:
    await kit.create(client, SLUG)
    await login(client, "dev@varga.dev")
    refused = await _declare(client, _config())
    assert refused.status_code == 403, refused.text


def test_a_connector_at_an_outpost_carries_the_service_identity_and_no_other() -> None:
    """ADR 0149 §2: a need that must be called as the person is refused when it is bound, since the
    type's closed schema has no `identity_modes`; and a row that had one would change nothing."""
    from choregos_api.db.models import OrgConnector
    from choregos_api.services.connector_identity import supports_identity

    forged = {"outpost": SLUG, "endpoint": ALIAS, "identity_modes": ["user_exchange", "user_consent"]}
    at_an_outpost = OrgConnector(kind="mcp", type="mcp-outpost", config=forged)
    assert supports_identity(at_an_outpost, "service")
    assert not supports_identity(at_an_outpost, "user_exchange")
    assert not supports_identity(at_an_outpost, "user_consent")
    direct = OrgConnector(kind="mcp", type="mcp", config={"identity_modes": ["user_exchange"]})
    assert supports_identity(direct, "user_exchange"), "a direct connector declares its own"


async def test_no_path_builds_its_client_without_the_seam(client: AsyncClient, admin: str) -> None:
    """The type cannot be built from its configuration alone, by the adapters' factory or by the
    platform's: only the one seam of connector calls supplies the rendezvous. A person's token handed
    to a direct build goes nowhere."""
    from choregos_adapters.errors import AdapterError
    from choregos_api.db.session import session_scope
    from choregos_api.services import plugin_servers

    await kit.create(client, SLUG)
    assert (await _declare(client, _config())).status_code == 201
    instance = await route.connector_row()
    async with session_scope(orgs="*") as session:
        for build in (
            plugin_servers.connector_client(session, instance),
            plugin_servers.connector_client(session, instance, bearer="a person's token"),
        ):
            with pytest.raises(AdapterError, match="only the platform's connector route supplies"):
                await build


async def test_the_route_names_the_outpost_and_the_alias_and_has_no_host(
    client: AsyncClient, admin: str
) -> None:
    """ADR 0149 §3: `route_of` answers "through an outpost" for the type, and for it alone."""
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope
    from choregos_api.services.mcp_gateway import Route, route_of

    await kit.create(client, SLUG)
    assert (await _declare(client, _config())).status_code == 201
    instance = await route.connector_row()
    async with session_scope(orgs="*") as session:
        reached = await route_of(session, instance)
        assert reached == Route(None, False, outpost=SLUG, endpoint=ALIAS)
        assert reached.via_outpost and not reached.via_gateway and reached.host is None
        direct = OrgConnector(
            org_id=instance.org_id,
            name="direct",
            kind="mcp",
            type="mcp",
            config={"url": "https://x.test/mcp"},
        )
        there = await route_of(session, direct)
        assert (there.host, there.via_outpost, there.outpost, there.endpoint) == ("x.test", False, None, None)


async def test_the_alias_is_never_read_as_a_host(client: AsyncClient, admin: str) -> None:
    """`endpoint` is a URL-shaped key for other types. For this one it is an alias (ADR 0149 §11):
    neither the organisation's deny list nor its allowed set judges it, at declaration or at a call."""
    from choregos_api.services.platform_network import connector_host

    assert connector_host({"outpost": SLUG, "endpoint": "tracker"}) is None
    assert connector_host({"endpoint": "tracker"}) == "tracker", "another type's `endpoint` is an address"
    ceiling = await client.put(
        f"{ORG}/policy-ceiling",
        json={"ceiling": {"network": {"allow_domains": ["example.com"], "deny_domains": [ALIAS]}}},
    )
    assert ceiling.status_code == 200, ceiling.text
    await kit.create(client, SLUG)
    assert (await _declare(client, _config())).status_code == 201


# ───────────────────────── under CHOREGOS_FAKES, without a secrets key ─────────────────────────


async def test_without_a_secrets_key_the_fake_outpost_serves_the_connector_in_memory(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`CHOREGOS_FAKES=1` on a deployment that has no key to seal a call's arguments under: the
    in-memory fake secret store is in force, and so is the in-memory `FakeOutpost`. The route is still
    judged on the outpost's own row; no call is written; the refusals are the same."""
    from choregos_adapters.fakes.outpost import FakeOutpost
    from choregos_api.secret_store import ENV_KEKS
    from choregos_api.services import outpost_route

    monkeypatch.delenv(ENV_KEKS)
    assert outpost_route.faked()
    served = route.server(answers={"search_issues": "two issues match"})
    fake = FakeOutpost(fallback=lambda: served)
    monkeypatch.setattr(choregos_adapters, "FAKE_OUTPOST", fake)
    await kit.create(client, SLUG)  # no instance was ever enrolled: the fake stands for the daemon
    await route.declare(client)
    found = await client.post(f"{CONNECTORS}/{CONNECTOR}/discover")
    assert found.status_code == 200 and len(found.json()["added"]) == 3, found.text
    assert (await route.open_operation(client, "search_issues")).status_code == 200
    headers = await route.agent_and_run(client, project, [CONNECTOR])
    search = f"{route.BASE}/{CONNECTOR}__search_issues"

    answer = await client.post(search, headers=headers, json={"query": "door"})
    assert answer.json()["status_code"] == 200, answer.text
    assert answer.json()["result"]["content"][0]["text"] == "two issues match"
    assert served.calls == [("search_issues", {"query": "door"})]
    assert await route.calls() == [], "no row: the rendezvous is the fake's own"
    assert [line.provider for line in await route.ledger()] == [f"mcp:{CONNECTOR}"]
    # What the fake was handed is what a daemon would have opened.
    assert [call["endpoint"] for call in fake.received] == [ALIAS, ALIAS]
    assert fake.received[-1]["asserted"]["run"] == route.RUN

    fake.go_offline()
    offline = (await client.post(search, headers=headers, json={"query": "door"})).json()
    assert (offline["status_code"], offline["result"]["code"]) == (503, "outpost_offline")
    assert offline["result"]["outpost"] == SLUG, "the refusal names the organisation's outpost"
    fake.come_back()
    fake.refuse_next("tool_not_allowed")
    refused = (await client.post(search, headers=headers, json={"query": "door"})).json()
    assert (refused["status_code"], refused["result"]["refusal"]) == (422, "tool_not_allowed")
    # The outpost's own row still decides whether it is handed anything.
    assert (await client.post(f"{ORG}/outposts/{SLUG}/pause")).status_code == 200
    paused = (await client.post(search, headers=headers, json={"query": "door"})).json()
    assert (paused["status_code"], paused["result"]["code"]) == (403, "outpost_paused")
    assert len(await route.ledger()) == 1, "only the call that reached the server is booked"
    assert (await client.post(f"{ORG}/outposts/{SLUG}/resume")).status_code == 200
    # The platform still decides alone that a write carries its key: a row opened behind the API.
    from choregos_api.db.models import ConnectorOperation
    from choregos_api.db.session import session_scope
    from choregos_api.services.mcp_gateway import connector_client
    from choregos_api.services.mcp_gateway_identity import CallIdentity
    from sqlalchemy import update

    async with session_scope(orgs="*") as session:
        opened = update(ConnectorOperation).where(ConnectorOperation.name == "create_issue")
        await session.execute(opened.values(policy="allowed"))
    instance = await route.connector_row()

    async def identity() -> CallIdentity:
        return CallIdentity(org="varga", org_id=instance.org_id, subject="action:a-1", purpose="action")

    from choregos_adapters.outpost import OutpostNotAdmitted

    async with session_scope(orgs="*") as session:
        tools = await connector_client(session, instance, identity)
        with pytest.raises(OutpostNotAdmitted, match="delivered at most once"):
            await tools.call_tool("create_issue", {"title": "door"})
    assert [name for name, _ in served.calls] == ["search_issues"], "the write reached no server"


async def test_with_a_secrets_key_the_rendezvous_is_the_real_one_even_under_fakes(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.services import outpost_route

    assert choregos_adapters.fakes_enabled() and not outpost_route.faked()
    daemon = await daemon_kit.approved(client)
    fake = route.outpost()
    await route.seen(client, daemon, fake)
    await route.declare(client)
    async with route.serving(client, daemon, fake):
        assert (await client.post(f"{CONNECTORS}/{CONNECTOR}/discover")).status_code == 200
    assert [row.kind for row in await route.calls()] == ["list_tools"], "a row, claimed at the door"


def test_the_platform_registers_the_factory_that_supplies_the_rendezvous() -> None:
    """As it does for the lexical memory: the adapters hold the client and the port, the API the
    rendezvous. A process of its own: nothing another test registered is taken for the application's."""
    import subprocess
    import sys

    probe = (
        "from choregos_adapters import registry, builtins\n"
        "assert registry._REGISTRY[('mcp', 'mcp-outpost')] is builtins._mcp_outpost_client\n"
        "from choregos_api.main import create_app\n"
        "create_app()\n"
        "from choregos_api import adapter_wiring\n"
        "assert registry._REGISTRY[('mcp', 'mcp-outpost')] is adapter_wiring._outpost_tools\n"
        "assert ('mcp', 'mcp-outpost') in registry.BUILT_UNDER_FAKES\n"
        "assert registry.spec_of('mcp', 'mcp-outpost').secret_fields == ()\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr[-2000:]

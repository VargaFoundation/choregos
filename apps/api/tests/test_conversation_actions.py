# SPDX-License-Identifier: Apache-2.0
"""Inline writes in a conversation (S63-10, ADR 0139 point 5, ADR 0030).

An assistant is offered the `inline` write needs of its installation; calling one PROPOSES an action, it never
reaches the source. The conversation's owner confirms it: the broker then calls the source with a token
exchanged for THAT person (S63-07), and the source stays the judge. The MCP door lists the proposals with a
link to the console and never executes. Owners may raise an operation to `console`, never lower it below the
manifest's floor.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.fakes.identity_provider import FakeIdentityProvider
from choregos_adapters.token_exchange import claims_of
from httpx import AsyncClient
from sqlalchemy import select

from .agent_scopes_kit import add_member, as_user
from .kits.assistant_door import ORG, offline  # noqa: F401
from .kits.conversations import _open, _say
from .kits.synthetic_installation import synthetic_installation  # noqa: F401
from .kits.user_exchange import (  # noqa: F401 - fixtures imported for their use here
    AUDIENCE,
    EMAIL,
    SUBJECT,
    _calls,
    _declare,
    _sign_in_sealed,
    idp,
    source,
)
from .test_agent_sessions import Upstream, _agent, upstream  # noqa: F401

# The installation of these tests is a row, not a plugin: its readiness is not their subject.
pytestmark = pytest.mark.usefixtures("synthetic_installation")

ME = "/api/v1/me/conversations"
AUDIENCE_GROUP = "grp-buyers"
WRITE = "orders__commander_poste"
NEEDS = [{"connector": "orders", "tools": ["commander_poste"]}]
ARGUMENTS = {"item": "laptop", "quantity": 2}


async def _installation(*, approval: str = "inline") -> dict[str, str]:
    """An installation whose need `orders` is bound to the declared connector `fournisseur`."""
    from choregos_api.db.models import (
        AssistantInstallation,
        AssistantToolBinding,
        ConnectorOperation,
        Organization,
        OrgConnector,
        User,
    )
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        connector = (
            await session.execute(select(OrgConnector).where(OrgConnector.name == "fournisseur"))
        ).scalar_one()
        operation = (
            await session.execute(
                select(ConnectorOperation).where(
                    ConnectorOperation.connector_id == connector.id,
                    ConnectorOperation.name == "commander_poste",
                )
            )
        ).scalar_one()
        operation.policy = "allowed"
        # S63-15: only the audience's groups use an assistant, an administrator included.
        (await session.execute(select(User).where(User.email == EMAIL))).scalar_one().groups = [
            AUDIENCE_GROUP
        ]
        installation = AssistantInstallation(
            org_id=org.id, slug="buyer", plugin="p", plugin_version="1", manifest_digest="sha256:0",
            assistant_name="buyer", status="ready", audience_groups=[AUDIENCE_GROUP],
        )  # fmt: skip
        session.add(installation)
        await session.flush()
        session.add(
            AssistantToolBinding(
                org_id=org.id, installation_id=installation.id, need="orders", connector_id=connector.id,
                operations=["commander_poste"], identity="user_exchange", approval=approval,
            )
        )  # fmt: skip
        return {"installation": installation.id, "org": org.id}


async def _ready(client: AsyncClient, *, approval: str = "inline") -> dict[str, str]:
    await _declare(client)
    await _sign_in_sealed()
    return await _installation(approval=approval)


async def _propose(
    client: AsyncClient,
    admin: str,
    upstream: Upstream,  # noqa: F811
    ids: dict[str, str],
) -> tuple[str, str]:
    await _agent(client, mcp_servers=NEEDS)
    upstream.model.call(WRITE, ARGUMENTS).say("I proposed the order; please confirm it.")
    conversation = await _open("varga", admin, installation_id=ids["installation"])
    answer = await _say(client, conversation, "Order two laptops.")
    assert answer.status_code == 200, answer.text
    (action,) = (await client.get(f"{ME}/{conversation}/actions")).json()["items"]
    return conversation, str(action["id"])


def _writes(source: Any) -> list[dict[str, str]]:  # noqa: F811
    return _calls(source)


async def test_a_write_is_proposed_and_never_reaches_the_source(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    source: Any,  # noqa: F811
    idp: FakeIdentityProvider,  # noqa: F811
) -> None:
    ids = await _ready(client)
    conversation, action_id = await _propose(client, admin, upstream, ids)

    (_, first) = upstream.model.requests[0]
    assert WRITE in {tool["function"]["name"] for tool in first["tools"]}, "the inline write is offered"
    (_, last) = upstream.model.requests[-1]
    told = [m["content"] for m in last["messages"] if m["role"] == "tool"]
    assert "awaits the person's confirmation" in told[0] and "NOTHING was executed" in told[0]

    (action,) = (await client.get(f"{ME}/{conversation}/actions")).json()["items"]
    assert action["id"] == action_id and action["status"] == "proposed"
    assert action["need"] == "orders" and action["operation"] == "commander_poste"
    assert action["arguments"] == ARGUMENTS and "laptop" in action["summary"] and action["result"] is None
    assert _writes(source) == [], "nothing reached the source"
    assert [r for r in idp.requests if r["grant_type"].endswith("token-exchange")] == []


async def test_a_confirmation_executes_with_the_persons_identity(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    source: Any,  # noqa: F811
    idp: FakeIdentityProvider,  # noqa: F811
) -> None:
    ids = await _ready(client)
    conversation, action_id = await _propose(client, admin, upstream, ids)

    confirmed = await client.post(f"{ME}/{conversation}/actions/{action_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "executed" and body["result"]["status_code"] == 200
    assert body["confirmed_by"] and body["executed_at"] is not None
    (headers,) = _writes(source)
    bearer = headers["authorization"].removeprefix("Bearer ")
    assert bearer != "supplier-key", "not the connector's own key"
    claims = claims_of(bearer)
    assert claims["sub"] == SUBJECT and claims["aud"] == AUDIENCE, "the person's token, for the source"
    again = await client.post(f"{ME}/{conversation}/actions/{action_id}/confirm")
    assert again.status_code == 409 and again.json()["code"] == "action_executed"
    assert len(_writes(source)) == 1, "an action is executed once"


async def test_a_refusal_executes_nothing(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    source: Any,  # noqa: F811
    idp: FakeIdentityProvider,  # noqa: F811
) -> None:
    ids = await _ready(client)
    conversation, action_id = await _propose(client, admin, upstream, ids)
    refused = await client.post(f"{ME}/{conversation}/actions/{action_id}/refuse")
    assert refused.status_code == 200 and refused.json()["status"] == "refused"
    assert (await client.post(f"{ME}/{conversation}/actions/{action_id}/confirm")).status_code == 409
    assert _writes(source) == []


async def test_another_person_cannot_confirm(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    source: Any,  # noqa: F811
    idp: FakeIdentityProvider,  # noqa: F811
) -> None:
    ids = await _ready(client)
    conversation, action_id = await _propose(client, admin, upstream, ids)
    await add_member(client, "other@varga.dev", "org_admin")
    await as_user(client, "other@varga.dev")
    for verb in ("confirm", "refuse"):
        response = await client.post(f"{ME}/{conversation}/actions/{action_id}/{verb}")
        assert response.status_code == 404, response.text
    assert (await client.get(f"{ME}/{conversation}/actions")).status_code == 404
    assert _writes(source) == []
    await as_user(client, EMAIL)
    assert (await client.get(f"{ME}/{conversation}/actions")).json()["items"][0]["status"] == "proposed"


async def test_a_console_approval_is_not_offered_and_a_raised_one_is_not_executed(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    source: Any,  # noqa: F811
    idp: FakeIdentityProvider,  # noqa: F811
) -> None:
    from choregos_api.db.models import AssistantToolBinding
    from choregos_api.db.session import session_scope

    ids = await _ready(client)
    conversation, action_id = await _propose(client, admin, upstream, ids)
    async with session_scope(orgs="*") as session:
        binding = (await session.execute(select(AssistantToolBinding))).scalar_one()
        binding.approval = "console"
    raised = await client.post(f"{ME}/{conversation}/actions/{action_id}/confirm")
    assert raised.status_code == 409 and raised.json()["code"] == "approval_console"
    assert _writes(source) == []
    upstream.model.say("hello")
    assert (await _say(client, conversation, "again")).status_code == 200
    (_, last) = upstream.model.requests[-1]
    assert WRITE not in {tool["function"]["name"] for tool in last.get("tools") or []}


async def test_a_read_conversation_without_installation_is_offered_no_write(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    source: Any,  # noqa: F811
    idp: FakeIdentityProvider,  # noqa: F811
) -> None:
    await _ready(client)
    await _agent(client, mcp_servers=NEEDS)
    upstream.model.say("hello")
    conversation = await _open("varga", admin)
    assert (await _say(client, conversation, "hi")).status_code == 200
    (_, first) = upstream.model.requests[0]
    assert WRITE not in {tool["function"]["name"] for tool in first.get("tools") or []}


async def test_the_mcp_door_lists_the_proposal_with_a_console_link_and_never_executes(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    source: Any,  # noqa: F811
    idp: FakeIdentityProvider,  # noqa: F811
) -> None:
    from choregos_api.db.models import AssistantInstallation
    from choregos_api.db.session import session_scope

    from .kits.mcp_assistants import _call, _names

    ids = await _ready(client)
    async with session_scope(orgs="*") as session:
        installation = await session.get(AssistantInstallation, ids["installation"])
        assert installation is not None
        installation.audience_groups = [AUDIENCE_GROUP]
    conversation, action_id = await _propose(client, admin, upstream, ids)
    await as_user(client, EMAIL)
    created = await client.post("/api/v1/me/tokens", json={"name": "mcp", "scopes": ["mcp:read"]})
    token = str(created.json()["token"])

    names = await _names(client, token)
    assert not {n for n in names if "confirm" in n}, "the door has no way to confirm"
    answer = await _call(client, token, "get_conversation", {"conversation_id": conversation})
    (pending,) = answer["pending_actions"]
    assert pending["id"] == action_id and "laptop" in pending["summary"]
    assert pending["confirm_url"].endswith(f"/conversations/{conversation}?action={action_id}")
    assert _writes(source) == [], "no call reached the connector through the door"
    # Still proposed after the door read it.
    assert (await client.get(f"{ME}/{conversation}/actions")).json()["items"][0]["status"] == "proposed"


# ───────────────────────── the floor of an approval ─────────────────────────


async def _variant(offline_root: Any) -> None:
    """The fixture plugin, with `orders` an inline-floor write and a console-floor write `refunds`."""
    import pathlib

    root = pathlib.Path(offline_root) / "investment-assistant" / "0.1.0"
    manifest = next((root / "assistants").glob("*.y*ml"))
    text = manifest.read_text()
    text = text.replace(
        "approval: console, required: false }",
        "approval: inline, required: false }\n  - { need: refunds, operations: [refund], access: write, "
        "identity: user_exchange, approval: console, required: false }",
    ).replace(
        "identity: user_consent,\n      approval: inline", "identity: user_exchange,\n      approval: inline"
    )
    manifest.write_text(text)
    agent = next((root / "agents").glob("*.y*ml"))
    agent.write_text(
        agent.read_text().replace(
            "      - { connector: orders, tools: [place_order] }\n",
            "      - { connector: orders, tools: [place_order] }\n"
            "      - { connector: refunds, tools: [refund] }\n",
        )
    )


async def _writing_connector(name: str, operation: str) -> None:
    from choregos_api.db.models import ConnectorOperation, Organization, OrgConnector
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        row = OrgConnector(
            org_id=org_id, name=name, kind="mcp", type="mcp",
            config={"url": f"https://{name}.test/mcp", "identity_modes": ["service", "user_exchange"]},
        )  # fmt: skip
        session.add(row)
        await session.flush()
        session.add(
            ConnectorOperation(
                org_id=org_id, connector_id=row.id, name=operation, access="write", policy="allowed"
            )
        )


async def test_owners_raise_an_approval_but_never_go_below_the_manifests_floor(
    client: AsyncClient,
    org: str,
    offline: Any,  # noqa: F811
) -> None:
    from .kits.assistant_door import install_plugin

    await _variant(offline)
    await install_plugin(client)
    created = await client.post(
        f"{ORG}/assistants",
        json={"plugin": "investment-assistant", "assistant": "investment-assistant",
              "config": {"house_name": "Varga"}},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    await _writing_connector("orders-source", "place_order")
    await _writing_connector("refunds-source", "refund")
    bind = f"{ORG}/assistants/investment-assistant/bindings"

    def stored(response: Any, need: str) -> Any:
        return next(b["approval"] for b in response.json()["bindings"] if b["need"] == need)

    # The floor of `refunds` is console: inline is below it, 422, and nothing is bound.
    below = await client.put(f"{bind}/refunds", json={"connector": "refunds-source", "approval": "inline"})
    assert below.status_code == 422, below.text
    assert "below" in below.text
    assert not [
        b for b in (await client.get(f"{ORG}/assistants/investment-assistant")).json()["bindings"]
        if b["need"] == "refunds"
    ]  # fmt: skip
    # Omitted, it is the floor; raised to console it is stored.
    bound = await client.put(f"{bind}/refunds", json={"connector": "refunds-source"})
    assert bound.status_code == 200 and stored(bound, "refunds") == "console"
    # `orders` floors at inline: inline and console are accepted, and the choice is kept.
    plain = await client.put(f"{bind}/orders", json={"connector": "orders-source"})
    assert plain.status_code == 200, plain.text
    assert stored(plain, "orders") == "inline"
    raised = await client.put(f"{bind}/orders", json={"connector": "orders-source", "approval": "console"})
    assert raised.status_code == 200 and stored(raised, "orders") == "console"
    # Binding again without a choice does not lower it below the floor either: it takes the floor.
    assert (
        stored(await client.put(f"{bind}/orders", json={"connector": "orders-source"}), "orders") == "inline"
    )
    # A read has no approval to choose.
    news = await client.put(f"{bind}/news", json={"connector": "orders-source", "approval": "console"})
    assert news.status_code == 422


@pytest.mark.parametrize("approval", ["none"])
async def test_an_approval_of_none_is_not_a_choice(client: AsyncClient, admin: str, approval: str) -> None:
    refused = await client.put(
        f"{ORG}/assistants/x/bindings/orders", json={"connector": "c", "approval": approval}
    )
    assert refused.status_code == 422

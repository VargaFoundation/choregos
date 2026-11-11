"""The standing delegation of a task (S63-14, ADR 0137 points 3-4).

A person agrees once that a read-only task reads on their behalf. Each guard has its test: the agreement is
the exact list of needs (409 shows it); a read while the person is offline goes out under a token exchanged
for them from the sealed OFFLINE token (the sign-in's token having been forgotten) and is journaled with the
delegation's id; it is refused after expiry (a simulated clock), after revocation (the token is revoked at
the provider and the sealed copy is gone), after the person left the audience, after the task's needs
changed in the installed manifest; a write under a delegation is a typed refusal that reaches no source; and
on PostgreSQL another person never reads someone's delegation.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_adapters.fakes.identity_provider import FakeIdentityProvider
from choregos_adapters.token_exchange import claims_of
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from .conftest import login, without_postgres
from .kits.assistant_install import FIXTURE, MARKET, NEWS, ORG, _bind, _connector, _install, _member, _plugin

AUDIENCE = "investors"
EMAIL = "traveller@varga.dev"
SUBJECT = f"dev|{EMAIL}"
SIGN_IN_REFRESH = "refresh-of-the-traveller"
ME = "/api/v1/me"
SLUG = "investment-assistant"
TASKS = f"{ME}/assistants/varga/{SLUG}/tasks"
AUDIENCE_OF_SOURCE = "markets-mcp"
LONG = 3650


WORKFLOW = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: morning-brief, version: 1}
actors:
  me: {type: human, group: investors}
states:
  brief: {display: Brief, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-done, from: brief, to: done, by: me}
"""


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2030, 1, 7, hour, minute, tzinfo=UTC)


TASKS_BLOCK: list[dict[str, Any]] = [
    {
        "name": "poll_alert",
        "workflow": "workflows/morning-brief.yaml",
        "triggers": [
            {
                "kind": "poll",
                "cron": "*/15 9-17 * * 1-5",
                "operation": "market_data__get_quote",
                "when": {"/events/*/change_pct": {"abs_gte": 3}},
                "idempotency": "/events/*/id",
            }
        ],
        "delegation": "read_only",
        "needs": ["market_data"],
        "deliver": ["inbox"],
    },
    {
        "name": "plain_brief",
        "workflow": "workflows/morning-brief.yaml",
        "triggers": [{"kind": "on_demand"}],
        "delegation": "none",
        "needs": ["news"],
        "deliver": ["inbox"],
    },
]


def _write_manifest(root: Path, version: str, *, extra_need: bool) -> None:
    target = root / "investment-assistant" / version
    assistant = target / "assistants" / "investment-assistant.yaml"
    document = yaml.safe_load(assistant.read_text())
    for tool in document["tools"]:
        if tool["need"] == "market_data":
            tool["identity"] = "user_exchange"
    tasks = [dict(t) for t in TASKS_BLOCK]
    if extra_need:
        tasks[0] = {**tasks[0], "needs": ["market_data", "news"]}
    document["tasks"] = tasks
    (target / "workflows" / "morning-brief.yaml").write_text(WORKFLOW, encoding="utf-8")
    assistant.write_text(yaml.safe_dump(document, sort_keys=False))


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Path]:
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    shutil.copytree(FIXTURE, root / "investment-assistant" / "0.1.0")
    _write_manifest(root, "0.1.0", extra_need=False)
    use_plugin_supply(OfflineDirectory(root))
    yield root
    use_plugin_supply(None)


@pytest.fixture
def markets(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The source: it accepts any bearer and records them, so a test reads WHICH one arrived."""
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    tools = [
        {
            "name": n,
            "description": n,
            "annotations": {"readOnlyHint": True},
            "inputSchema": {"type": "object"},
        }
        for n in MARKET
    ]
    server = FakeMcpServer(tools=tools, token="")
    server.structured["get_quote"] = {"events": [{"id": "e1", "change_pct": 4.2}]}
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_MARKETS_KEY", "markets-key")
    yield server


@pytest.fixture
async def idp() -> AsyncIterator[FakeIdentityProvider]:
    from choregos_api.services.identity_exchange import set_exchange_transport

    provider = FakeIdentityProvider(refresh_tokens={SIGN_IN_REFRESH: SUBJECT})
    set_exchange_transport(provider.transport())
    yield provider
    set_exchange_transport(None)


async def _as(app: Any, email: str) -> AsyncClient:
    person = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(person, email)
    return person


async def _ready(client: AsyncClient) -> None:
    await _plugin(client)
    await _connector("news-feed", NEWS)
    installed = await _install(client, audience_groups=[AUDIENCE])
    assert installed.status_code == 201, installed.text
    assert (await _bind(client, "news", "news-feed")).status_code == 200
    config = {
        "url": "https://markets.test/mcp",
        "identity_modes": ["service", "user_exchange"],
        "token_exchange": {"audience": AUDIENCE_OF_SOURCE},
    }
    body = {
        "name": "markets",
        "type": "mcp",
        "config": config,
        "secret_refs": {"token": "env:CONNECTOR_TEST_MARKETS_KEY"},
    }
    assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    assert (await client.post(f"{ORG}/connectors/markets/discover")).status_code == 200
    for operation in MARKET:
        opened = await client.patch(
            f"{ORG}/connectors/markets/operations/{operation}", json={"policy": "allowed"}
        )
        assert opened.status_code == 200, opened.text
    ready = await _bind(client, "market_data", "markets")
    assert ready.json()["status"] == "ready", ready.text
    capped = await client.patch(
        f"{ORG}/assistants/{SLUG}", json={"policy_overrides": {"delegation": {"max_days": LONG}}}
    )
    assert capped.status_code == 200, capped.text


async def _user_id(email: str = EMAIL) -> tuple[str, str]:
    from choregos_api.db.models import Organization, User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        return user.id, org.id


async def _sign_in_sealed() -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.identity_exchange import seal_refresh_token

    user_id, org_id = await _user_id()
    async with session_scope(orgs="*") as session:
        await seal_refresh_token(session, [org_id], user_id, SIGN_IN_REFRESH)


async def _forget_sign_in() -> None:
    """The person is away: their sign-in token is not there any more."""
    from choregos_api.db.session import session_scope
    from choregos_api.services.identity_exchange import forget_refresh_token

    user_id, org_id = await _user_id()
    async with session_scope(orgs="*") as session:
        await forget_refresh_token(session, [org_id], user_id)


async def _traveller(client: AsyncClient, app: Any) -> AsyncClient:
    await _member(client, EMAIL, [AUDIENCE])
    await _sign_in_sealed()
    person = await _as(app, EMAIL)
    put = await person.put(f"{TASKS}/poll_alert", json={"kind": "poll"})
    assert put.status_code == 200, put.text
    return person


def _agreement(**extra: Any) -> dict[str, Any]:
    return {"org": "varga", "assistant": SLUG, "task": "poll_alert", "agree_to": ["market_data"], **extra}


async def _agree(person: AsyncClient, **extra: Any) -> dict[str, Any]:
    response = await person.post(f"{ME}/delegations", json=_agreement(**extra))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _pass(now: datetime) -> dict[str, str]:
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_tasks import run_due

    async with session_scope(orgs="*") as session:
        return await run_due(session, now)


async def _subscription() -> Any:
    from choregos_api.db.models import TaskSubscription
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        sub = (await session.execute(select(TaskSubscription))).scalar_one()
        session.expunge(sub)
        return sub


async def _delegation(delegation_id: str) -> Any:
    from choregos_api.db.models import Delegation
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        row = await session.get(Delegation, delegation_id)
        assert row is not None
        session.expunge(row)
        return row


async def _sealed(name: str) -> str | None:
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import get_secret_store
    from choregos_core.secrets import SecretNotFound

    _, org_id = await _user_id()
    async with session_scope(orgs="*") as session:
        try:
            return (await get_secret_store().get_many(session, org_id, [name])).get(name)
        except SecretNotFound:
            return None


def _calls(source: Any) -> list[dict[str, str]]:
    return [headers for method, headers, _ in source.received if method == "tools/call"]


async def _reads() -> list[Any]:
    from choregos_api.db.models import AssistantRead
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = list((await session.execute(select(AssistantRead))).scalars())
        for row in rows:
            session.expunge(row)
        return rows


# ───────────────────────── the agreement ─────────────────────────


async def test_the_agreement_is_the_exact_list_of_needs_and_the_offline_token_is_sealed(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    await _ready(client)
    person = await _traveller(client, app)

    for agree in ([], ["news"], ["market_data", "news"]):
        refused = await person.post(f"{ME}/delegations", json=_agreement(agree_to=agree))
        assert refused.status_code == 409, refused.text
        assert refused.json()["agree_to"] == ["market_data"]
        (need,) = refused.json()["needs"]
        assert need["need"] == "market_data" and "get_quote" in need["operations"]
    assert idp.offline_tokens == set(), "nothing was asked of the provider before the person agreed"

    granted = await _agree(person)
    assert granted["status"] == "active" and granted["task"] == "poll_alert"
    assert [n["need"] for n in granted["needs"]] == ["market_data"]
    assert len(idp.offline_tokens) == 1
    offline_token = next(iter(idp.offline_tokens))
    assert offline_token not in str(granted), "the token is never returned"
    assert await _sealed(f"user/{(await _user_id())[0]}/delegation/{granted['id']}") == offline_token
    assert (await _subscription()).delegation_id == granted["id"], "linked to the subscription"
    listed = (await person.get(f"{ME}/delegations")).json()["items"]
    assert [d["id"] for d in listed] == [granted["id"]] and listed[0]["expires_at"]


async def test_the_expiry_defaults_to_ninety_days_and_the_installation_caps_it(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    await _ready(client)
    person = await _traveller(client, app)

    def days(body: dict[str, Any]) -> float:
        granted = datetime.fromisoformat(body["granted_at"])
        return (datetime.fromisoformat(body["expires_at"]) - granted).total_seconds() / 86400

    assert round(days(await _agree(person, expires_in_days=None))) == 90
    cap = await client.patch(
        f"{ORG}/assistants/{SLUG}", json={"policy_overrides": {"delegation": {"max_days": 30}}}
    )
    assert cap.status_code == 200, cap.text
    assert round(days(await _agree(person, expires_in_days=400))) == 30, "capped by the installation"
    assert round(days(await _agree(person, expires_in_days=7))) == 7
    active = [d for d in (await person.get(f"{ME}/delegations")).json()["items"] if d["status"] == "active"]
    assert len(active) == 1, "a new agreement replaces the previous one"


async def test_what_cannot_be_delegated_is_refused(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    await _ready(client)
    person = await _traveller(client, app)
    none = await person.post(f"{ME}/delegations", json=_agreement(task="plain_brief", agree_to=["news"]))
    assert none.status_code == 422, "`delegation: none`: nothing to delegate"
    unknown = await person.post(f"{ME}/delegations", json=_agreement(task="nope"))
    assert unknown.status_code == 404
    outsider = await _as(app, "outsider@varga.dev")
    assert (await outsider.post(f"{ME}/delegations", json=_agreement())).status_code == 404, (
        "outside the audience"
    )
    await _member(client, "not-subscribed@varga.dev", [AUDIENCE])
    other = await _as(app, "not-subscribed@varga.dev")
    assert (await other.post(f"{ME}/delegations", json=_agreement())).status_code == 409, "subscribe first"


async def test_a_write_need_cannot_be_delegated() -> None:
    from choregos_api.errors import ApiError
    from choregos_api.services.assistant_delegations import agreement_of

    document = {"tools": [{"need": "orders", "operations": ["place_order"], "access": "write"}]}
    (found,) = agreement_of(document, {"needs": ["orders"]})
    assert found["access"] == "write"
    with pytest.raises(ApiError):
        agreement_of(document, {"needs": ["unknown"]})


# ───────────────────────── a delegated read, offline ─────────────────────────


async def test_a_delegated_read_works_while_the_person_is_offline_and_is_journaled(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    await _ready(client)
    person = await _traveller(client, app)
    granted = await _agree(person, expires_in_days=LONG)
    await _forget_sign_in()  # the person is away: no sign-in token is left, only the delegation's

    started = await _pass(at(9, 0))
    assert list(started.values()) == ["poll:e1"], started
    (headers,) = _calls(markets)
    bearer = headers["authorization"].removeprefix("Bearer ")
    claims = claims_of(bearer)
    assert claims["sub"] == SUBJECT and claims["aud"] == AUDIENCE_OF_SOURCE, (
        "a token of the PERSON for the source"
    )
    assert claims["act"]["sub"] == f"assistant:{SLUG}"
    assert bearer != "markets-key"
    (read,) = await _reads()
    assert read.delegation_id == granted["id"] and read.need == "market_data"
    assert read.operation == "market_data__get_quote" and read.conversation_id is None

    audit = await client.get(f"{ORG}/assistants/{SLUG}/reads")
    assert audit.status_code == 200, audit.text
    assert [i["delegation_id"] for i in audit.json()["items"]] == [granted["id"]]
    # Each read exchanges again: one more call, one more line.
    markets.structured["get_quote"] = {"events": [{"id": "e2", "change_pct": 5}]}
    assert list((await _pass(at(9, 15))).values()) == ["poll:e2"]
    assert len(_calls(markets)) == 2 and len(await _reads()) == 2


async def test_without_a_delegation_the_scheduled_run_is_refused_and_reads_nothing(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    await _ready(client)
    person = await _traveller(client, app)
    assert await _pass(at(9, 0)) == {}
    refused = (await person.get(f"{TASKS}/poll_alert")).json()["last_refusal"]
    assert "standing delegation" in refused and "/me/delegations" in refused
    assert _calls(markets) == [] and await _reads() == []


# ───────────────────────── the refusals ─────────────────────────


async def test_the_delegation_is_refused_after_its_expiry_by_the_clock(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    await _ready(client)
    person = await _traveller(client, app)
    granted = await _agree(person, expires_in_days=1)
    assert await _pass(at(9, 0)) == {}, "a day later it is expired (2030 against a grant of today)"
    assert "expired" in (await person.get(f"{TASKS}/poll_alert")).json()["last_refusal"]
    row = await _delegation(granted["id"])
    assert row.status == "suspended" and "expired" in row.suspended_reason
    assert _calls(markets) == [] and await _reads() == []


async def test_the_delegation_is_refused_after_revocation_and_the_token_is_killed(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    await _ready(client)
    person = await _traveller(client, app)
    granted = await _agree(person, expires_in_days=LONG)
    user_id, _ = await _user_id()
    name = f"user/{user_id}/delegation/{granted['id']}"
    offline_token = await _sealed(name)
    assert offline_token

    assert list((await _pass(at(9, 0))).values()) == ["poll:e1"]
    gone = await person.delete(f"{ME}/delegations/{granted['id']}")
    assert gone.status_code == 204, gone.text
    assert idp.revoked == [offline_token] and offline_token not in idp.refresh_tokens, (
        "revoked at the provider"
    )
    assert await _sealed(name) is None, "the sealed copy is deleted"
    markets.structured["get_quote"] = {"events": [{"id": "e9", "change_pct": 9}]}
    before = len(_calls(markets))
    assert await _pass(at(9, 15)) == {}
    assert "revoked" in (await person.get(f"{TASKS}/poll_alert")).json()["last_refusal"]
    assert len(_calls(markets)) == before, "the next delegated read reaches no source"
    assert (await person.get(f"{ME}/delegations")).json()["items"][0]["status"] == "revoked"


async def test_the_delegation_is_refused_when_the_person_leaves_the_audience(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope

    await _ready(client)
    person = await _traveller(client, app)
    granted = await _agree(person, expires_in_days=LONG)

    async def groups(value: list[str]) -> None:
        async with session_scope(orgs="*") as session:
            user = (await session.execute(select(User).where(User.email == EMAIL))).scalar_one()
            user.groups = value

    await groups([])
    assert await _pass(at(9, 0)) == {}
    assert "audience" in (await _subscription()).last_refusal
    assert (await _delegation(granted["id"])).status == "suspended"
    assert _calls(markets) == []
    await groups([AUDIENCE])  # back in the audience: the same delegation serves again
    assert list((await _pass(at(9, 15))).values()) == ["poll:e1"]
    assert (await _delegation(granted["id"])).status == "active"


async def test_the_delegation_is_refused_when_the_person_is_no_longer_a_member(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    from choregos_api.db.models import Membership
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete

    await _ready(client)
    person = await _traveller(client, app)
    await _agree(person, expires_in_days=LONG)
    user_id, _ = await _user_id()
    async with session_scope(orgs="*") as session:
        await session.execute(delete(Membership).where(Membership.user_id == user_id))
    assert await _pass(at(9, 0)) == {}
    assert _calls(markets) == []


async def test_the_delegation_is_refused_when_the_needs_of_the_task_changed(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    await _ready(client)
    person = await _traveller(client, app)
    granted = await _agree(person, expires_in_days=LONG)
    assert list((await _pass(at(9, 0))).values()) == ["poll:e1"]

    target = offline / "investment-assistant" / "0.2.0"
    shutil.copytree(offline / "investment-assistant" / "0.1.0", target)
    plugin = target / "plugin.yaml"
    plugin.write_text(plugin.read_text().replace("version: 0.1.0", "version: 0.2.0"))
    _write_manifest(offline, "0.2.0", extra_need=True)  # the task now also reads `news`
    upgraded = await client.post(f"{ORG}/plugins/investment-assistant/upgrade", json={"version": "0.2.0"})
    assert upgraded.status_code == 200, upgraded.text

    markets.structured["get_quote"] = {"events": [{"id": "e7", "change_pct": 8}]}
    before = len(_calls(markets))
    assert await _pass(at(9, 15)) == {}
    assert "changed" in (await person.get(f"{TASKS}/poll_alert")).json()["last_refusal"]
    assert len(_calls(markets)) == before
    assert (await _delegation(granted["id"])).status == "suspended"
    # A new agreement, to the new list, serves again.
    stale = await person.post(f"{ME}/delegations", json=_agreement())
    assert stale.status_code == 409 and stale.json()["agree_to"] == ["market_data", "news"]
    await _agree(person, agree_to=["market_data", "news"], expires_in_days=LONG)
    assert list((await _pass(at(9, 30))).values()) == ["poll:e7"]


# ───────────────────────── a write under a delegation ─────────────────────────


async def test_a_write_under_a_delegation_is_a_typed_refusal_that_reaches_no_source(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    from choregos_api.db.models import AssistantInstallation, ConnectorOperation, Delegation
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_delegations import DelegationRefused
    from choregos_api.services.assistant_tasks import read_operation

    await _ready(client)
    person = await _traveller(client, app)
    granted = await _agree(person, expires_in_days=LONG)
    async with session_scope(orgs="*") as session:
        operation = (
            await session.execute(select(ConnectorOperation).where(ConnectorOperation.name == "get_quote"))
        ).scalar_one()
        operation.access = "write"  # the source's operation now changes something
    async with session_scope(orgs="*") as session:
        row = (await session.execute(select(AssistantInstallation))).scalar_one()
        delegation = await session.get(Delegation, granted["id"])
        with pytest.raises(DelegationRefused) as refused:
            await read_operation(session, row, "market_data__get_quote", {}, delegation=delegation, now=at(9))
    assert refused.value.code == "write_under_delegation"
    assert "no conversation" in str(refused.value), "a task has no conversation to propose it in"
    assert _calls(markets) == [] and await _reads() == []
    assert await _pass(at(9, 0)) == {}
    assert "write_under_delegation" in (await person.get(f"{TASKS}/poll_alert")).json()["last_refusal"]


async def test_a_delegated_read_outside_the_agreement_is_refused(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    from choregos_api.db.models import AssistantInstallation, Delegation
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_delegations import DelegationRefused
    from choregos_api.services.assistant_tasks import read_operation

    await _ready(client)
    person = await _traveller(client, app)
    granted = await _agree(person, expires_in_days=LONG)
    async with session_scope(orgs="*") as session:
        row = (await session.execute(select(AssistantInstallation))).scalar_one()
        delegation = await session.get(Delegation, granted["id"])
        with pytest.raises(DelegationRefused) as refused:  # `news` is bound, but not agreed to
            await read_operation(session, row, "news__search_news", {}, delegation=delegation, now=at(9))
    assert refused.value.code == "outside_agreement"


@pytest.mark.parametrize("drift", ["identity", "connector"])
async def test_a_need_bound_otherwise_than_when_the_person_agreed_suspends_the_delegation(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,
    markets: Any,
    idp: FakeIdentityProvider,
    drift: str,
) -> None:
    """Security review S63-14: the agreement names the need, but the person agreed to read it AS THEM, at the
    source it was bound to. An administrator who re-binds it (to the connector's own identity, or to another
    source) must not make the standing token read there: a new agreement is needed, and nothing is called."""
    from choregos_api.db.models import AssistantToolBinding, OrgConnector
    from choregos_api.db.session import session_scope

    await _ready(client)
    person = await _traveller(client, app)
    granted = await _agree(person, expires_in_days=LONG)
    async with session_scope(orgs="*") as session:
        binding = (
            await session.execute(
                select(AssistantToolBinding).where(AssistantToolBinding.need == "market_data")
            )
        ).scalar_one()
        if drift == "identity":
            binding.identity = "service"
        else:
            other = (
                await session.execute(select(OrgConnector).where(OrgConnector.name == "news-feed"))
            ).scalar_one()
            binding.connector_id = other.id
    before = len(_calls(markets))
    assert await _pass(at(9, 0)) == {}
    assert "not bound as it was" in (await person.get(f"{TASKS}/poll_alert")).json()["last_refusal"]
    assert len(_calls(markets)) == before, "no source was called under any identity"
    assert (await _delegation(granted["id"])).status == "suspended"


async def test_a_delegated_read_of_an_operation_the_person_did_not_agree_to_is_refused(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    from choregos_api.db.models import AssistantInstallation, AssistantToolBinding, Delegation
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_delegations import DelegationRefused, guard_read

    await _ready(client)
    person = await _traveller(client, app)
    granted = await _agree(person, expires_in_days=LONG)
    async with session_scope(orgs="*") as session:
        row = (await session.execute(select(AssistantInstallation))).scalar_one()
        binding = (
            await session.execute(
                select(AssistantToolBinding).where(AssistantToolBinding.need == "market_data")
            )
        ).scalar_one()
        delegation = await session.get(Delegation, granted["id"])
        assert delegation is not None
        with pytest.raises(DelegationRefused) as refused:
            await guard_read(
                session,
                delegation,
                row,
                need="market_data",
                access="read",
                binding=binding,
                operation="export_everything",
                now=at(9),
            )
    assert refused.value.code == "outside_agreement"


# ───────────────────────── another person, and erasure ─────────────────────────


async def test_another_person_never_sees_or_revokes_someones_delegation(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    await _ready(client)
    person = await _traveller(client, app)
    granted = await _agree(person, expires_in_days=LONG)
    await _member(client, "colleague@varga.dev", [AUDIENCE])
    colleague = await _as(app, "colleague@varga.dev")
    assert (await colleague.get(f"{ME}/delegations")).json()["items"] == []
    assert (await client.get(f"{ME}/delegations")).json()["items"] == [], "not even an administrator's"
    assert (await colleague.delete(f"{ME}/delegations/{granted['id']}")).status_code == 404
    assert (await client.delete(f"{ME}/delegations/{granted['id']}")).status_code == 404
    assert (await _delegation(granted["id"])).status == "active"


async def test_erasing_a_person_from_an_assistant_removes_their_delegations_and_tokens(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, idp: FakeIdentityProvider
) -> None:
    await _ready(client)
    person = await _traveller(client, app)
    granted = await _agree(person, expires_in_days=LONG)
    user_id, _ = await _user_id()
    name = f"user/{user_id}/delegation/{granted['id']}"
    erased = await person.delete(f"{ME}/assistants/varga/{SLUG}")
    assert erased.status_code == 200, erased.text
    assert erased.json()["others"]["delegations"] == 1
    assert (await person.get(f"{ME}/delegations")).json()["items"] == []
    assert await _sealed(name) is None and len(idp.revoked) == 1


# ───────────────────────── PostgreSQL: RLS ─────────────────────────


async def test_the_table_is_under_forced_rls_with_an_owner_only_policy() -> None:
    from choregos_api.db.models import Delegation

    assert Delegation.__tablename__ == "delegations"
    migration = Path(__file__).parent.parent / "src/choregos_api/migrations/versions"
    text = next(migration.glob("*delegations.py")).read_text()
    assert "FORCE ROW LEVEL SECURITY" in text and "AS RESTRICTIVE" in text
    assert "user_id = choregos_utilisateur_courant()" in text


@pytest.fixture
async def organisation_a(pg_app: Any) -> str:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        organisation = Organization(slug="a", name="A")
        session.add(organisation)
        await session.flush()
        return organisation.id


@without_postgres
async def test_another_person_never_reads_someones_delegation_on_postgresql(
    pg_app: Any, organisation_a: str
) -> None:
    from choregos_api.db.models import (
        AssistantInstallation,
        Delegation,
        Organization,
        User,
    )
    from choregos_api.db.session import limit_to_organisations, name_the_user, session_scope
    from sqlalchemy.exc import DBAPIError

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as http:
        await login(http, "admin@a.test")
        await login(http, "dev@a.test")
    async with session_scope(orgs="*") as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "a"))).scalar_one()
        users = {u.email: u.id for u in (await session.execute(select(User))).scalars()}
        installation = AssistantInstallation(
            org_id=org.id,
            slug="x",
            plugin="p",
            plugin_version="1",
            manifest_digest="d",
            assistant_name="x",
            audience_groups=[],
            owner_groups=[],
        )
        session.add(installation)
        await session.flush()
        now = datetime.now(UTC)
        session.add(
            Delegation(
                org_id=org.id,
                installation_id=installation.id,
                user_id=users["dev@a.test"],
                task="t",
                needs=[],
                granted_at=now,
                expires_at=now,
                manifest_digest="d",
            )
        )
        org_id, installation_id = org.id, installation.id
    async with session_scope(orgs=["a"]) as session:
        await name_the_user(session, users["admin@a.test"])
        assert (await session.execute(select(Delegation))).all() == [], "not even an administrator"
    async with session_scope(orgs=["a"]) as session:
        await name_the_user(session, users["dev@a.test"])
        assert len((await session.execute(select(Delegation))).all()) == 1
    async with session_scope() as session:  # no user named: nothing
        await limit_to_organisations(session, ["a"])
        assert (await session.execute(select(Delegation))).all() == []
    with pytest.raises(DBAPIError):  # nor writes one in someone else's name
        async with session_scope(orgs=["a"]) as session:
            await name_the_user(session, users["admin@a.test"])
            now = datetime.now(UTC)
            session.add(
                Delegation(
                    org_id=org_id,
                    installation_id=installation_id,
                    user_id=users["dev@a.test"],
                    task="u",
                    needs=[],
                    granted_at=now,
                    expires_at=now,
                    manifest_digest="d",
                )
            )
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(Delegation))).all()) == 1, "the platform scope reads"

"""What the platform reaches on an agent's behalf obeys the organisation's network policy (ADR 0070):
connectors, knowledge sources, git skill sources — refused at declaration (422) and at call (403),
throttled per host (429), and identified by a User-Agent that carries the organisation's contact."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from .test_courtier import ORG, _preparer, fournisseur  # noqa: F401  (a fixture, used by name)

CEILING = f"{ORG}/policy-ceiling"
BASE = "/api/v1/orgs/varga/knowledge"
MCP = {"name": "fournisseur", "type": "mcp", "config": {"url": "https://fournisseur.test/mcp"}}


async def _ceiling(client: AsyncClient, network: dict[str, Any]) -> None:
    done = await client.put(CEILING, json={"ceiling": {"network": network}})
    assert done.status_code == 200, done.text


async def _decisions() -> list[Any]:
    from choregos_api.db.models import NetworkDecision
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(NetworkDecision))).scalars())


# ───────────────────────────── connectors: declaration ─────────────────────────────


async def test_a_connector_outside_the_ceiling_is_refused_at_creation(
    client: AsyncClient, admin: str
) -> None:
    await _ceiling(client, {"allow_domains": ["vendor.example"]})
    refused = await client.post(f"{ORG}/connectors", json=MCP)
    assert refused.status_code == 422, refused.text
    assert "fournisseur.test" in refused.text and "not_allowed" in refused.text
    assert (await client.get(f"{ORG}/connectors")).json() == [], "nothing was created"
    inside = {**MCP, "config": {"url": "https://api.vendor.example/mcp"}}
    assert (await client.post(f"{ORG}/connectors", json=inside)).status_code == 201


async def test_a_catalogue_install_outside_the_ceiling_is_refused_and_creates_nothing(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The MCP catalogue (ADR 0065) installs through `declare_org_connector`, the same door: the ceiling applies."""
    monkeypatch.setenv("CONNECTOR_CATALOGUE_NETWORK_TEST", "a-test-value")
    await _ceiling(client, {"allow_domains": ["vendor.example"]})
    refused = await client.post(
        f"{ORG}/mcp-catalogue/github/install", json={"secret_refs": {"token": "env:CONNECTOR_CATALOGUE_NETWORK_TEST"}}
    )
    assert refused.status_code == 422, refused.text
    assert "api.githubcopilot.com" in refused.text and "not_allowed" in refused.text
    assert (await client.get(f"{ORG}/connectors")).json() == [], "nothing was created"
    await _ceiling(client, {"allow_domains": ["githubcopilot.com"]})
    allowed = await client.post(
        f"{ORG}/mcp-catalogue/github/install", json={"secret_refs": {"token": "env:CONNECTOR_CATALOGUE_NETWORK_TEST"}}
    )
    assert allowed.status_code == 201, allowed.text


async def test_a_denied_host_is_refused_even_when_everything_else_is_allowed(
    client: AsyncClient, admin: str
) -> None:
    await _ceiling(client, {"deny_domains": ["fournisseur.test"]})
    refused = await client.post(f"{ORG}/connectors", json=MCP)
    assert refused.status_code == 422 and "denied:fournisseur.test" in refused.text


async def test_an_update_cannot_move_a_connector_outside_the_ceiling(client: AsyncClient, admin: str) -> None:
    await _ceiling(client, {"allow_domains": ["fournisseur.test"]})
    assert (await client.post(f"{ORG}/connectors", json=MCP)).status_code == 201
    moved = await client.patch(
        f"{ORG}/connectors/fournisseur", json={"config": {"url": "https://elsewhere.example/mcp"}}
    )
    assert moved.status_code == 422 and "elsewhere.example" in moved.text


async def test_an_internal_address_is_not_the_internet(client: AsyncClient, admin: str) -> None:
    """A cap on the internet does not cut the organisation's own appliances off."""
    await _ceiling(client, {"allow_domains": ["vendor.example"], "deny_domains": ["blocked.example"]})
    for url in ("https://10.201.0.1:8888/api", "http://pfsense/api", "https://svc.cluster.local/x"):
        body = {**MCP, "name": "a" + str(abs(hash(url)))[:6], "config": {"url": url}}
        created = await client.post(f"{ORG}/connectors", json=body)
        assert (created.status_code == 201) == ("cluster.local" not in url), (url, created.text)


# ───────────────────────────── connectors: at the call ─────────────────────────────


async def test_a_ceiling_tightened_after_creation_refuses_the_call_and_journals_it(
    client: AsyncClient,
    project: dict[str, Any],
    fournisseur: Any,  # noqa: F811
) -> None:
    base, headers = await _preparer(client, project, ["*"])
    assert (await client.post(f"{base}/fournisseur__stock", headers=headers, json={})).json()[
        "status_code"
    ] == 200
    reached = len(fournisseur.appels)
    await _ceiling(client, {"deny_domains": ["fournisseur.test"]})
    for _ in range(2):
        answer = await client.post(f"{base}/fournisseur__stock", headers=headers, json={})
        assert answer.status_code == 200 and answer.json()["status_code"] == 403, answer.text
        assert "network_policy" in str(answer.json()["result"])
        assert "fournisseur.test" in answer.json()["result"]["error"]
    assert len(fournisseur.appels) == reached, "the server was not reached"
    (row,) = await _decisions()
    assert (row.layer, row.host, row.decision, row.count) == ("broker", "fournisseur.test", "denied", 2)
    assert row.run_id == "run-courtier-1" and row.project_id == project["id"]
    assert row.reason == "denied:fournisseur.test"


async def test_discovery_obeys_the_ceiling_too(client: AsyncClient, admin: str) -> None:
    assert (await client.post(f"{ORG}/connectors", json=MCP)).status_code == 201
    await _ceiling(client, {"allow_domains": ["vendor.example"]})
    refused = await client.post(f"{ORG}/connectors/fournisseur/discover")
    assert refused.status_code == 403, refused.text
    assert refused.json()["type"] == "network_policy"


async def test_a_governed_write_reads_the_ceiling_again_when_it_runs(
    client: AsyncClient,
    project: dict[str, Any],
    fournisseur: Any,  # noqa: F811
) -> None:
    """The effect of an approved action is played later, by a workflow: it must not reach a host the
    organisation has closed since."""
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope
    from choregos_api.effets import ContexteEffet, EffetRefuse, _appel_de_connecteur
    from sqlalchemy import select

    await _preparer(client, project, ["*"])
    await _ceiling(client, {"deny_domains": ["fournisseur.test"]})
    async with session_scope() as session:
        stored = (await session.execute(select(Project).where(Project.id == project["id"]))).scalar_one()
        action = Action(id="a1", project_id=project["id"], kind="x", title="t", status="approved")
        with pytest.raises(EffetRefuse, match=r"fournisseur\.test"):
            await _appel_de_connecteur(
                ContexteEffet(session, action, stored),
                {"connector": "fournisseur", "operation": "stock", "arguments": {}},
            )
    assert fournisseur.appels == []


async def test_the_organisation_calls_a_host_at_most_n_times_a_minute(
    client: AsyncClient,
    project: dict[str, Any],
    fournisseur: Any,  # noqa: F811
) -> None:
    base, headers = await _preparer(client, project, ["*"])
    await _ceiling(client, {"per_host_per_minute": 2})
    codes = []
    for _ in range(4):
        codes.append(
            (await client.post(f"{base}/fournisseur__stock", headers=headers, json={})).json()["status_code"]
        )
    assert codes == [200, 200, 429, 429]
    assert len(fournisseur.appels) == 2, "the throttled calls did not reach the server"
    rows = {(r.decision, r.reason): r.count for r in await _decisions()}
    assert rows == {("rate_limited", "per_host_per_minute:2"): 2}


async def test_the_rate_is_counted_over_the_last_minute_only(
    client: AsyncClient,
    project: dict[str, Any],
    fournisseur: Any,  # noqa: F811
) -> None:
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope
    from choregos_api.services.platform_network import calls_to_host
    from choregos_core import utcnow
    from sqlalchemy import select

    base, headers = await _preparer(client, project, ["*"])
    for _ in range(3):
        await client.post(f"{base}/fournisseur__stock", headers=headers, json={})
    async with session_scope() as session:
        org_id = (await session.execute(select(OrgConnector.org_id))).scalars().first()
        assert org_id is not None
        assert await calls_to_host(session, org_id, "fournisseur.test") == 3
        assert await calls_to_host(session, org_id, "other.example") == 0
        later = utcnow() + timedelta(seconds=61)
        assert await calls_to_host(session, org_id, "fournisseur.test", now=later) == 0, "frozen clock"


# ───────────────────────────── the User-Agent ─────────────────────────────


async def test_mcp_calls_carry_the_organisations_contact(
    client: AsyncClient,
    project: dict[str, Any],
    fournisseur: Any,  # noqa: F811
) -> None:
    from choregos_api import __version__

    base, headers = await _preparer(client, project, ["*"])
    await _ceiling(client, {"user_agent_contact": "security@ops.example"})
    assert (await client.post(f"{base}/fournisseur__stock", headers=headers, json={})).json()[
        "status_code"
    ] == 200
    agents = {recus["user-agent"] for _, recus, _ in fournisseur.recues[-3:]}
    assert agents == {f"Choregos/{__version__} (+security@ops.example)"}


async def test_the_user_agent_falls_back_to_the_deployments_contact_then_the_project_page(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import get_settings
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.platform_network import DEFAULT_CONTACT, user_agent
    from sqlalchemy import select

    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        assert (await user_agent(session, org_id)).endswith(f"(+{DEFAULT_CONTACT})")
        monkeypatch.setattr(get_settings(), "user_agent_contact", "ops@deploy.example")
        assert (await user_agent(session, org_id)).endswith("(+ops@deploy.example)")


# ───────────────────────────── knowledge sources ─────────────────────────────


async def test_a_web_source_outside_the_ceiling_is_refused_at_creation(
    client: AsyncClient, admin: str
) -> None:
    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    await _ceiling(client, {"allow_domains": ["docs.example.com"]})
    body = {"kind": "web", "name": "w", "config": {"url": "https://wiki.other.test/"}}
    refused = await client.post(f"{BASE}/framework/sources", json=body)
    assert refused.status_code == 422 and "wiki.other.test" in refused.text, refused.text
    body["config"] = {"url": "https://docs.example.com/"}
    assert (await client.post(f"{BASE}/framework/sources", json=body)).status_code == 201
    git = {"kind": "git", "name": "g", "config": {"url": "https://git.other.test/p.git"}}
    assert (await client.post(f"{BASE}/framework/sources", json=git)).status_code == 422


async def test_a_ceiling_tightened_after_creation_stops_the_next_sync(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.knowledge.sync import SourceUnavailableError, sync_source

    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    created = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "web", "name": "Runbooks", "config": {"url": "https://docs.example.com/"}},
    )
    assert created.status_code == 201, created.text
    await _ceiling(client, {"deny_domains": ["example.com"]})
    with pytest.raises(SourceUnavailableError, match=r"docs\.example\.com"):
        await sync_source("varga", created.json()["id"])
    shown = (await client.get(f"{BASE}/framework")).json()["sources"][0]
    assert shown["status"] == "failed" and "denied:example.com" in shown["last_error"]


# ───────────────────────────── git skill sources ─────────────────────────────


@pytest.fixture
def recorded_skills() -> Any:
    from choregos_api.services.skill_sources import FakeSourceFetcher, set_fetcher

    from .test_skills_from_git import ANTHROPICS, REPO_V1, SHA1, tarball

    fetcher = FakeSourceFetcher()
    fetcher.add(ANTHROPICS, SHA1, tarball(REPO_V1), refs={"main": SHA1})
    set_fetcher(fetcher)
    yield fetcher
    set_fetcher(None)


async def test_a_skill_source_outside_the_ceiling_is_refused_and_an_allowed_one_is_identified(
    client: AsyncClient, admin: str, recorded_skills: Any
) -> None:
    from .test_skills_from_git import ANTHROPICS

    preview = f"{ORG}/skills/import-git/preview"
    request = {"repo_url": ANTHROPICS, "ref": "main", "path": "skills"}
    await _ceiling(client, {"deny_domains": ["github.com"], "user_agent_contact": "ops@ops.example"})
    refused = await client.post(preview, json=request)
    assert refused.status_code == 422 and "github.com" in refused.text, refused.text
    assert recorded_skills.calls == [], "nothing was fetched"
    await _ceiling(client, {"allow_domains": ["github.com"], "user_agent_contact": "ops@ops.example"})
    done = await client.post(preview, json=request)
    assert done.status_code == 200, done.text
    assert recorded_skills.user_agents and all(
        ua.endswith("(+ops@ops.example)") and ua.startswith("Choregos/") for ua in recorded_skills.user_agents
    )

"""The agent catalogue, seen from an organisation (ADR 0040, S21-17).

Installing never overwrites; an upgrade publishes the next version only when the catalogue changed; an
organisation's own agent of the same name is never touched; a workflow or a template that names a
catalogue agent installs it. Since S43-09 the catalogue is the offline plugins' (ADR 0093 §6): what it
installs carries the plugin's provenance, `plugin:<plugin>/<slug>@<version>`.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

import pytest
from choregos_api.schemas import AgentCreate
from choregos_api.services.agents import version_errors
from choregos_core.agent_catalogue import entries, entry
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"


def _replace_entry(monkeypatch: pytest.MonkeyPatch, new: Any) -> None:
    """The offline catalogue with `new` in place of the entry of the same slug."""
    from choregos_api.services import agent_catalogue as service

    base = service.offline_catalogue()
    kept = (*(e for e in base.entries if e.slug != new.slug), new)
    view = dataclasses.replace(base, entries=kept)
    monkeypatch.setattr(service, "offline_catalogue", lambda: view)


def test_every_catalogue_entry_is_an_installable_agent() -> None:
    """Each entry's document passes `AgentCreate` and its instructions render: a broken entry would
    show here, not at the first organisation that installs it."""
    for proposal in entries():
        body = AgentCreate.model_validate(proposal.document)
        version_errors(body.spec)


async def test_the_catalogue_lists_the_agents_and_says_whether_they_are_installed(
    client: AsyncClient, admin: str
) -> None:
    listed = (await client.get(f"{ORG}/agent-catalogue")).json()
    by_slug = {e["slug"]: e for e in listed}
    assert {"developer", "tester", "architect", "claude-code"} <= set(by_slug)
    assert by_slug["developer"]["role"] == "implement" and by_slug["developer"]["installed"] is False
    assert by_slug["chatgpt"]["reach"] == "cloud" and by_slug["claude-code"]["kind"] == "external"
    assert (await client.post(f"{ORG}/agent-catalogue/developer/install")).status_code == 201
    developer = next(
        e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "developer"
    )
    assert (developer["installed"], developer["installed_version"], developer["update_available"]) == (
        True,
        1,
        False,
    )


async def test_installing_an_agent_installs_its_skills_first(client: AsyncClient, admin: str) -> None:
    installed = await client.post(f"{ORG}/agent-catalogue/architect/install")
    assert installed.status_code == 201, installed.text
    agent = installed.json()
    assert agent["versions"][0]["created_by"] == "plugin:software-delivery/architect@3"
    assert agent["versions"][0]["spec"]["skills"] == [{"slug": "madr-4", "version": None}]
    skills = {s["slug"]: s for s in (await client.get(f"{ORG}/skills")).json()}
    assert skills["madr-4"]["latest_version"] == 1


async def test_reinstalling_does_not_rewrite_an_existing_agent(client: AsyncClient, admin: str) -> None:
    assert (await client.post(f"{ORG}/agent-catalogue/tester/install")).status_code == 201
    again = await client.post(f"{ORG}/agent-catalogue/tester/install")
    assert again.status_code == 409 and "upgrade" in again.text


async def test_an_upgrade_publishes_the_next_version_only_if_it_changes(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.post(f"{ORG}/agent-catalogue/triager/install")).status_code == 201
    same = await client.post(f"{ORG}/agent-catalogue/triager/install", json={"upgrade": True})
    assert same.status_code == 200 and same.json()["latest_version"] == 1, (
        "nothing changed: nothing published"
    )

    old = entry("triager")
    assert old is not None
    document = {**old.document, "spec": {**old.document["spec"], "instructions": "Triage it, briefly."}}
    new = dataclasses.replace(old, version=2, document=document)
    _replace_entry(monkeypatch, new)
    listed_entry = next(
        e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "triager"
    )
    assert listed_entry["update_available"] is True
    published = await client.post(f"{ORG}/agent-catalogue/triager/install", json={"upgrade": True})
    assert published.status_code == 200, published.text
    versions = published.json()["versions"]
    assert [v["version"] for v in versions] == [2, 1], "version 1 is not rewritten"
    assert versions[0]["spec"]["instructions"] == "Triage it, briefly."


async def test_an_organisations_own_agent_of_the_same_name_is_never_touched(
    client: AsyncClient, admin: str
) -> None:
    own = await client.post(f"{ORG}/agents", json={"slug": "developer", "display_name": "Our developer"})
    assert own.status_code == 201
    assert (
        await client.post(f"{ORG}/agent-catalogue/developer/install", json={"upgrade": True})
    ).status_code == 409
    listed_entry = next(
        e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "developer"
    )
    assert (listed_entry["own_agent"], listed_entry["update_available"]) == (True, False)
    agent = (await client.get(f"{ORG}/agents/developer")).json()
    assert agent["display_name"] == "Our developer" and agent["latest_version"] == 1


async def test_only_an_administrator_installs(client: AsyncClient, admin: str) -> None:
    from .conftest import login

    member = await client.post(f"{ORG}/members", json={"email": "dev@varga.dev", "role": "developer"})
    assert member.status_code in {200, 201}
    await login(client, "dev@varga.dev")
    assert (await client.get(f"{ORG}/agent-catalogue")).status_code == 200, "a member reads the catalogue"
    assert (await client.post(f"{ORG}/agent-catalogue/developer/install")).status_code == 403
    assert (await client.post(f"{ORG}/agent-catalogue/nobody/install")).status_code in {403, 404}


async def test_an_agent_unknown_to_the_catalogue_returns_404(client: AsyncClient, admin: str) -> None:
    assert (await client.post(f"{ORG}/agent-catalogue/nobody/install")).status_code == 404


async def test_publishing_a_workflow_that_names_a_catalogue_agent_installs_it(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_core.dsl import template_yaml

    yaml = template_yaml("default-simple").replace(
        'dev: { type: agent, role: implement, model: "profile:by_size" }',
        'dev: { type: agent, role: implement, model: "profile:by_size", agent: developer }',
    )
    assert "agent: developer" in yaml
    published = await client.put(
        f"/api/v1/projects/{project['id']}/workflows/default-simple", json={"yaml": yaml}
    )
    assert published.status_code == 200, published.text
    agent = (await client.get(f"{ORG}/agents/developer")).json()
    assert agent["versions"][0]["created_by"] == "plugin:software-delivery/developer@2"


@pytest.fixture
def templates(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A template that names an agent and a skill of the platform catalogue."""
    root = tmp_path / "templates"
    folder = root / "catalogue-trial"
    folder.mkdir(parents=True)
    (folder / "manifest.yaml").write_text(
        "apiVersion: choregos/v1\nkind: Template\nmetadata: {name: catalogue-trial, version: 1.0.0}\n"
        "defaults:\n  workflows: ['template:default-simple@1']\n"
        "  skills: [catalogue:madr-4]\n  agents: [catalogue:reviewer, catalogue:nobody-knows]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", str(root))
    return folder


async def test_a_template_can_name_a_catalogue_agent(
    client: AsyncClient, admin: str, templates: pathlib.Path
) -> None:
    body = {
        "slug": "hr",
        "name": "hr",
        "template_ref": "catalogue-trial",
        "config": {"slug": "hr", "org": "varga"},
    }
    refused = await client.post(f"{ORG}/projects", json=body)
    assert refused.status_code == 422 and "catalogue:nobody-knows" in refused.text, (
        "an unknown name is refused"
    )
    (templates / "manifest.yaml").write_text(
        (templates / "manifest.yaml").read_text(encoding="utf-8").replace(", catalogue:nobody-knows", ""),
        encoding="utf-8",
    )
    created = await client.post(f"{ORG}/projects", json=body)
    assert created.status_code == 201, created.text
    reviewer = (await client.get(f"{ORG}/agents/reviewer")).json()
    assert reviewer["versions"][0]["spec"]["skills"] == [{"slug": "madr-4", "version": None}]
    assert "madr-4" in {s["slug"] for s in (await client.get(f"{ORG}/skills")).json()}


# ───────────────────────── external clients in one click (S21-18) ─────────────────────────


async def test_connecting_claude_code_creates_the_agent_mints_an_mcp_token_and_attaches_it(
    client: AsyncClient, admin: str
) -> None:
    connected = await client.post(f"{ORG}/agent-catalogue/claude-code/connect")
    assert connected.status_code == 201, connected.text
    body = connected.json()
    assert (body["agent"]["slug"], body["agent"]["kind"]) == ("claude-code", "external")
    assert body["token"]["scopes"] == ["mcp:write"] and body["token"]["token"], "the token, returned once"
    assert body["oauth_client_id"] is None
    attachments = (await client.get(f"{ORG}/agents/claude-code/credentials")).json()
    assert [r["token_name"] for r in attachments] == ["Claude Code"]
    tokens = (await client.get("/api/v1/me/tokens")).json()
    assert "token" not in tokens[0], "read back, the token is not shown any more"


async def test_a_member_joins_a_clients_agent_but_does_not_create_it(client: AsyncClient, admin: str) -> None:
    from .conftest import login

    member = await client.post(f"{ORG}/members", json={"email": "dev@varga.dev", "role": "developer"})
    assert member.status_code in {200, 201}
    await login(client, "dev@varga.dev")
    assert (await client.post(f"{ORG}/agent-catalogue/cursor/connect")).status_code == 403, (
        "creating takes an administrator"
    )
    await login(client, "admin@varga.dev")
    assert (await client.post(f"{ORG}/agent-catalogue/cursor/connect")).status_code == 201
    await login(client, "dev@varga.dev")
    joined = await client.post(f"{ORG}/agent-catalogue/cursor/connect", json={"read_only": True})
    assert joined.status_code == 201, joined.text
    assert joined.json()["token"]["scopes"] == ["mcp:read"]
    await login(client, "admin@varga.dev")
    assert len((await client.get(f"{ORG}/agents/cursor/credentials")).json()) == 2, "one token per person"


async def test_an_internal_agent_does_not_connect(client: AsyncClient, admin: str) -> None:
    assert (await client.post(f"{ORG}/agent-catalogue/developer/connect")).status_code == 404


async def test_a_client_that_calls_from_the_cloud_is_not_offered_without_oauth_or_https(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from choregos_api.config import reset_settings_cache

    chatgpt = next(e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "chatgpt")
    assert chatgpt["offered"] is False and "OAuth" in chatgpt["unavailable_reason"]
    refused = await client.post(f"{ORG}/agent-catalogue/chatgpt/connect")
    assert refused.status_code == 409 and "not offered" in refused.text

    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_ISSUER", "https://sso.example/realms/varga")
    monkeypatch.setenv(
        "CHOREGOS_MCP_OAUTH_CLIENTS", json.dumps({"chatgpt": {"client_id": "choregos-chatgpt"}})
    )
    reset_settings_cache()
    still = next(e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "chatgpt")
    assert still["offered"] is False and "https" in still["unavailable_reason"], "OAuth, but over http"

    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", "https://choregos.example")
    reset_settings_cache()
    claude_ai = next(
        e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "claude-ai"
    )
    assert claude_ai["offered"] is False and "claude-ai" in claude_ai["unavailable_reason"], (
        "no client for it"
    )
    connected = await client.post(f"{ORG}/agent-catalogue/chatgpt/connect")
    assert connected.status_code == 201, connected.text
    assert (connected.json()["token"], connected.json()["oauth_client_id"]) == (None, "choregos-chatgpt")
    attached = (await client.get(f"{ORG}/agents/chatgpt/credentials")).json()
    assert [(r["kind"], r["client_id"]) for r in attached] == [("oauth_client", "choregos-chatgpt")]


# ──── a catalogue agent installed by a template upgrades like a direct one (S39-25, #674) ────


def _the_catalogue_moves_to_version_two(monkeypatch: pytest.MonkeyPatch, slug: str) -> None:
    old = entry(slug)
    assert old is not None
    document = {**old.document, "spec": {**old.document["spec"], "instructions": "Review it, v2."}}
    _replace_entry(monkeypatch, dataclasses.replace(old, version=2, document=document))


async def _project_of_the_template(client: AsyncClient, templates: pathlib.Path) -> None:
    (templates / "manifest.yaml").write_text(
        (templates / "manifest.yaml").read_text(encoding="utf-8").replace(", catalogue:nobody-knows", ""),
        encoding="utf-8",
    )
    body = {
        "slug": "hr",
        "name": "hr",
        "template_ref": "catalogue-trial",
        "config": {"slug": "hr", "org": "varga"},
    }
    assert (await client.post(f"{ORG}/projects", json=body)).status_code == 201


async def test_a_catalogue_agent_installed_by_a_template_carries_the_same_provenance_as_a_direct_one(
    client: AsyncClient, admin: str, templates: pathlib.Path
) -> None:
    await _project_of_the_template(client, templates)
    agent = (await client.get(f"{ORG}/agents/reviewer")).json()
    assert agent["versions"][0]["created_by"] == "plugin:software-delivery/reviewer@2"
    assert agent["scope"] == "platform"
    listed_entry = next(
        e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "reviewer"
    )
    assert listed_entry["own_agent"] is False


async def test_a_catalogue_agent_installed_by_a_template_upgrades_from_the_catalogue(
    client: AsyncClient, admin: str, templates: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _project_of_the_template(client, templates)
    _the_catalogue_moves_to_version_two(monkeypatch, "reviewer")
    published = await client.post(f"{ORG}/agent-catalogue/reviewer/install", json={"upgrade": True})
    assert published.status_code == 200, published.text
    assert [v["version"] for v in published.json()["versions"]] == [2, 1]
    assert published.json()["versions"][0]["spec"]["instructions"] == "Review it, v2."


async def test_a_diverged_agent_is_not_overwritten_by_the_upgrade(
    client: AsyncClient, admin: str, templates: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import Agent, AgentVersion
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _project_of_the_template(client, templates)
    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == "reviewer"))).scalar_one()
        first = (
            await session.execute(select(AgentVersion).where(AgentVersion.agent_id == agent.id))
        ).scalar_one()
        first.created_by += "!diverged"
        session.add(
            AgentVersion(agent_id=agent.id, org_id=agent.org_id, version=2, spec=first.spec,
                         checksum="sha256:by-hand", created_by="alice@varga.dev")
        )  # fmt: skip
        await session.commit()
    _the_catalogue_moves_to_version_two(monkeypatch, "reviewer")
    refused = await client.post(f"{ORG}/agent-catalogue/reviewer/install", json={"upgrade": True})
    assert refused.status_code == 409 and "published its own text" in refused.text, refused.text
    assert (await client.get(f"{ORG}/agents/reviewer")).json()["latest_version"] == 2


def test_a_manifest_names_the_catalogue_provenance_of_the_agents_it_takes_from_it() -> None:
    from choregos_api.services.templates import delivery

    reviewer = entry("reviewer")
    assert reviewer is not None
    manifest = {"defaults": {"workflows": ["template:default-simple@1"], "agents": ["catalogue:reviewer"]}}
    assert delivery(manifest, None).catalogue_agents == {
        "reviewer": f"plugin:software-delivery/reviewer@{reviewer.version}"
    }


# -- an organisation's knowledge bases survive an upgrade (ADR 0127, amendment) ----------------------------


async def _hand_version(slug: str, **changes: Any) -> None:
    """The organisation publishes the next version of `slug`: its latest spec with `changes`."""
    from choregos_api.db.models import Agent, AgentVersion
    from choregos_api.db.session import session_scope
    from choregos_api.schemas import AgentSpec
    from choregos_api.services.agents import digest
    from sqlalchemy import select

    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == slug))).scalar_one()
        last = (
            (
                await session.execute(
                    select(AgentVersion)
                    .where(AgentVersion.agent_id == agent.id)
                    .order_by(AgentVersion.version.desc())
                )
            )
            .scalars()
            .first()
        )
        assert last is not None
        spec = AgentSpec.model_validate({**last.spec, **changes})
        session.add(
            AgentVersion(
                agent_id=agent.id,
                org_id=agent.org_id,
                version=last.version + 1,
                spec=spec.model_dump(mode="json"),
                checksum=digest(spec),
                created_by="alice@varga.dev",
            )
        )
        await session.commit()


def _catalogue_moves(monkeypatch: pytest.MonkeyPatch, slug: str, **spec: Any) -> None:
    old = entry(slug)
    assert old is not None
    document = {**old.document, "spec": {**old.document["spec"], "instructions": "Review it, v2.", **spec}}
    _replace_entry(monkeypatch, dataclasses.replace(old, version=2, document=document))


async def test_an_upgrade_keeps_the_knowledge_the_organisation_added(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.post(f"{ORG}/agent-catalogue/reviewer/install")).status_code == 201
    await _hand_version("reviewer", knowledge=[{"slug": "our-engineering"}])
    _catalogue_moves(monkeypatch, "reviewer")
    listed = next(e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "reviewer")
    assert listed["update_available"] is True
    published = await client.post(f"{ORG}/agent-catalogue/reviewer/install", json={"upgrade": True})
    assert published.status_code == 200, published.text
    latest = published.json()["versions"][0]
    assert latest["version"] == 3 and latest["spec"]["instructions"] == "Review it, v2."
    assert [k["slug"] for k in latest["spec"]["knowledge"]] == ["our-engineering"]
    assert "knowledge kept: our-engineering" in latest["message"]
    again = await client.post(f"{ORG}/agent-catalogue/reviewer/install", json={"upgrade": True})
    assert again.json()["latest_version"] == 3, "nothing left to publish"


async def test_another_change_than_knowledge_is_not_kept(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.post(f"{ORG}/agent-catalogue/reviewer/install")).status_code == 201
    await _hand_version("reviewer", knowledge=[{"slug": "our-engineering"}], instructions="Ours.")
    _catalogue_moves(monkeypatch, "reviewer")
    published = await client.post(f"{ORG}/agent-catalogue/reviewer/install", json={"upgrade": True})
    latest = published.json()["versions"][0]
    assert latest["spec"].get("knowledge", []) == [] and "kept" not in (latest["message"] or "")


async def test_a_knowledge_base_the_catalogue_adds_too_is_not_listed_twice(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.post(f"{ORG}/agent-catalogue/reviewer/install")).status_code == 201
    await _hand_version("reviewer", knowledge=[{"slug": "shared"}, {"slug": "ours"}])
    _catalogue_moves(monkeypatch, "reviewer", knowledge=[{"slug": "shared"}])
    published = await client.post(f"{ORG}/agent-catalogue/reviewer/install", json={"upgrade": True})
    latest = published.json()["versions"][0]
    assert [k["slug"] for k in latest["spec"]["knowledge"]] == ["shared", "ours"]

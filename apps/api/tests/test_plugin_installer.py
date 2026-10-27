"""Installing a plugin into an organisation (S43-04, ADR 0093).

Each guard has its test: install twice changes nothing; an upgrade publishes the next version of an
agent nobody changed and only OFFERS it to one the organisation changed; uninstalling removes what
nobody changed and keeps the rest; the full `AgentCreate` validation and the plugin's own evals refuse
before anything is written; an organisation's own agent of the same name wins; templates come with
their files; procedures are served by `GET /procedures` to the organisation's members only; rights per
ADR 0074; every answer has the contract's shape.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import choregos_contracts as contracts
import pytest
import yaml
from choregos_core import template_yaml
from httpx import AsyncClient
from jsonschema import Draft202012Validator
from sqlalchemy import func, select

from .agent_scopes_kit import add_member, as_user, make_project
from .catalogue_kit import project_only

ORG = "/api/v1/orgs/varga"
HELLO = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "hello"
INSTALL = {"source": "offline", "name": "hello", "version": "1.0.0"}


def _edit(path: Path, change: Any) -> None:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _version(root: Path, version: str, instructions: str | None = None, *, server: bool = True) -> Path:
    """A copy of `hello` at `version`; new instructions bump the agent's catalogue version. Without its
    server, the plugin installs on one administrator's word (no second approval)."""
    target = root / "hello" / version
    shutil.copytree(HELLO, target)
    _edit(target / "plugin.yaml", lambda d: d["metadata"].update(version=version))
    if not server:
        shutil.rmtree(target / "servers")
        _edit(target / "plugin.yaml", lambda d: d["contents"].pop("servers"))
    if instructions is not None:

        def change(doc: dict[str, Any]) -> None:
            doc["catalogue"]["version"] = 2
            doc["agent"]["spec"]["instructions"] = instructions

        _edit(target / "agents" / "greeter.yaml", change)
    return target


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Path]:
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    first = _version(root, "1.0.0")
    # The template ships its own workflow file: proof that a plugin's template carries its files.
    workflow = template_yaml("default-simple").replace("name: default-simple", "name: hello-simple", 1)
    (first / "templates" / "hello-flow" / "workflow.yaml").write_text(workflow, encoding="utf-8")
    _edit(
        first / "templates" / "hello-flow" / "manifest.yaml",
        lambda d: d["defaults"].update(workflow="workflow.yaml"),
    )
    _version(
        root, "1.1.0", "Greet the author of the work item by name, warmly, in one sentence.", server=False
    )
    use_plugin_supply(OfflineDirectory(root))
    yield root
    use_plugin_supply(None)


SECOND = "second-admin@varga.dev"


async def _install(client: AsyncClient, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """`hello` brings a server: the administrator asks (202), a second administrator approves, and the
    plugin is installed. Returns the installed plugin as listed; the client is the first admin again."""
    asked = await client.post(f"{ORG}/plugins/install", json=body or INSTALL)
    assert asked.status_code == 202, asked.text
    await add_member(client, SECOND, "org_admin")
    await as_user(client, SECOND)
    approved = await client.post(f"{ORG}/plugin-approvals/{asked.json()['approval_id']}/approve")
    assert approved.status_code == 200, approved.text
    await as_user(client, "admin@varga.dev")
    listed = (await client.get(f"{ORG}/plugins")).json()["items"]
    return dict(next(p for p in listed if p["name"] == (body or INSTALL)["name"]))


def _contract(name: str) -> Draft202012Validator:
    spec = contracts.load_openapi()
    return Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": spec["components"]})


async def _counts() -> dict[str, int]:
    from choregos_api.db.models import (
        Agent,
        AgentVersion,
        AuditLog,
        OrgPlugin,
        OrgPluginFile,
        Skill,
        SkillVersion,
    )
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return {
            model.__tablename__: (await session.execute(select(func.count()).select_from(model))).scalar_one()
            for model in (Agent, AgentVersion, Skill, SkillVersion, OrgPlugin, OrgPluginFile, AuditLog)
        }


async def _versions(slug: str) -> list[tuple[int, str]]:
    from choregos_api.db.models import Agent, AgentVersion
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = await session.execute(
            select(AgentVersion.version, AgentVersion.created_by)
            .join(Agent, Agent.id == AgentVersion.agent_id)
            .where(Agent.slug == slug)
            .order_by(AgentVersion.version)
        )
        return [(v, str(by)) for v, by in rows.tuples()]


async def _hand_version(slug: str) -> None:
    """The organisation publishes its own text: the agent has diverged from the plugin."""
    from choregos_api.db.models import Agent, AgentVersion
    from choregos_api.db.session import session_scope

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
        session.add(
            AgentVersion(
                agent_id=agent.id,
                org_id=agent.org_id,
                version=last.version + 1,
                spec={**last.spec, "instructions": "Our own greeting."},
                checksum="sha256:" + "0" * 64,
                created_by="admin@varga.dev",
            )
        )


# ───────────────────────────── install ─────────────────────────────


async def test_install_brings_agents_skills_templates_evals_procedures_and_records_servers(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    from choregos_api.db.models import Agent, OrgPlugin
    from choregos_api.db.session import session_scope

    body = await _install(client)
    assert not list(_contract("InstalledPlugin").iter_errors(body))
    assert body["contents"] == {
        "agents": ["greeter"],
        "skills": ["greeting"],
        "templates": ["plugin:hello/hello-flow"],
        "evals": ["greeter/greeter"],
        "procedures": ["connector.mcp.hello"],
        "servers": ["hello"],
    }
    # The one who asked installed it; the second administrator approved it.
    assert body["installed_by"] == "admin@varga.dev" and body["latest"] == "1.1.0"
    assert body["digest"].startswith("sha256:") and body["default"] is False
    assert await _versions("greeter") == [(1, "plugin:hello/greeter@1")]
    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == "greeter"))).scalar_one()
        assert agent.scope == "platform"
        row = (await session.execute(select(OrgPlugin))).scalar_one()
        # The server runs with the plugin (S43-06, ADR 0094): `test_plugin_servers.py` proves how.
        assert row.status == "installed" and set(row.details["servers"]) == {"hello"}
    listed = (await client.get(f"{ORG}/plugins")).json()
    assert not list(_contract("PluginList").iter_errors(listed))
    assert [p["name"] for p in listed["items"]] == ["hello"]


async def test_install_twice_changes_nothing(client: AsyncClient, admin: str, offline: Path) -> None:
    from choregos_api.config import get_settings
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import SYSTEM
    from choregos_api.services import plugin_installer
    from choregos_api.services.plugin_supply import OfflineDirectory

    await _install(client)
    before = await _counts()
    again = await client.post(f"{ORG}/plugins/install", json=INSTALL)
    assert again.status_code == 409 and "already installed" in again.text
    # The path the start-up reconcile replays: the same content is a no-op, not a second install.
    async with session_scope() as session:
        org_id = await plugin_installer.org_id_of(session, "varga")
        async with OfflineDirectory(offline).obtain("offline", "hello", "1.0.0") as plugin:
            _, changed = await plugin_installer.install(
                session, org_id, plugin, "offline", get_settings(), SYSTEM
            )
    assert changed is False
    assert await _counts() == before


async def test_a_bad_agent_file_is_refused_by_the_full_agent_model_and_nothing_is_written(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    _edit(
        offline / "hello" / "1.0.0" / "agents" / "greeter.yaml",
        lambda d: d["agent"]["spec"].update(not_a_field=True),
    )
    before = await _counts()
    refused = await client.post(f"{ORG}/plugins/install", json=INSTALL)
    assert refused.status_code == 422, refused.text
    assert "greeter" in refused.text and "not_a_field" in refused.text
    assert await _counts() == before


async def test_a_plugin_whose_own_eval_fails_is_refused(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    _edit(
        offline / "hello" / "1.0.0" / "evals" / "greeter.yaml",
        lambda d: d.update(must_contain=["a sentence the instructions never say"]),
    )
    before = await _counts()
    refused = await client.post(f"{ORG}/plugins/install", json=INSTALL)
    assert refused.status_code == 422 and "eval suite" in refused.text
    assert await _counts() == before


ALONE = {"source": "offline", "name": "hello", "version": "1.1.0"}  # no server: one administrator


async def test_a_deterministic_eval_case_is_played_and_a_live_one_is_not(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    suite = offline / "hello" / "1.1.0" / "evals" / "greeter.yaml"
    live = {"name": "live", "live": True, "input": {"title": "Hi"}, "must_contain": ["only a model says"]}
    failing = {"name": "names-the-author", "input": {"title": "Hi"}, "must_contain": ["never in the text"]}
    _edit(suite, lambda d: d.update(cases=[live, failing]))
    before = await _counts()
    refused = await client.post(f"{ORG}/plugins/install", json=ALONE)
    assert refused.status_code == 422, refused.text
    assert "names-the-author" in refused.text and "live" not in refused.json()["errors"][0]["loc"]
    assert await _counts() == before

    failing["must_contain"] = ["Greet the author"]
    _edit(suite, lambda d: d.update(cases=[live, failing]))
    installed = await client.post(f"{ORG}/plugins/install", json=ALONE)
    assert installed.status_code == 201, installed.text


async def test_a_high_risk_skill_makes_a_plugin_wait_for_a_second_administrator(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    skill = offline / "hello" / "1.1.0" / "skills" / "greeting"
    (skill / "greet.sh").write_text("#!/bin/sh\necho hello\n", encoding="utf-8")
    before = await _counts()
    asked = await client.post(f"{ORG}/plugins/install", json=ALONE)
    assert asked.status_code == 202, asked.text
    assert "skill `skills/greeting` is high risk (script)" in asked.json()["reason"]
    assert await _counts() == {**before, "audit_log": (await _counts())["audit_log"]}


async def test_an_agent_of_the_organisation_with_the_same_name_wins(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    own = await client.post(
        f"{ORG}/agents", json={"slug": "greeter", "display_name": "Ours", "spec": {"instructions": "Ours."}}
    )
    assert own.status_code == 201, own.text
    installed = await _install(client)
    assert installed["contents"]["agents"] == []
    assert [by for _, by in await _versions("greeter")] == ["admin@varga.dev"]
    assert (await client.delete(f"{ORG}/plugins/hello")).status_code == 200
    assert [by for _, by in await _versions("greeter")] == ["admin@varga.dev"], "never the plugin's to remove"


async def test_a_missing_plugin_or_version_is_a_404(client: AsyncClient, admin: str, offline: Path) -> None:
    assert (await client.post(f"{ORG}/plugins/install", json={**INSTALL, "name": "nope"})).status_code == 404
    assert (
        await client.post(f"{ORG}/plugins/install", json={**INSTALL, "version": "9.9.9"})
    ).status_code == 404


# ───────────────────────────── upgrade ─────────────────────────────


async def test_an_upgrade_publishes_the_next_version_of_an_agent_nobody_changed(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    await _install(client)
    upgraded = await client.post(f"{ORG}/plugins/hello/upgrade", json={"version": "1.1.0"})
    assert upgraded.status_code == 200, upgraded.text
    body = upgraded.json()
    assert not list(_contract("PluginUpgradeResult").iter_errors(body))
    assert (body["previous_version"], body["plugin"]["version"], body["offered"]) == ("1.0.0", "1.1.0", [])
    assert body["plugin"]["latest"] is None
    assert await _versions("greeter") == [(1, "plugin:hello/greeter@1"), (2, "plugin:hello/greeter@2")]
    again = await client.post(f"{ORG}/plugins/hello/upgrade")
    assert again.status_code == 200 and len(await _versions("greeter")) == 2, (
        "same content: nothing published"
    )


async def test_an_upgrade_offers_and_never_overwrites_an_agent_the_organisation_changed(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    await _install(client)
    await _hand_version("greeter")
    upgraded = await client.post(f"{ORG}/plugins/hello/upgrade", json={"version": "1.1.0"})
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["offered"] == ["greeter"]
    assert await _versions("greeter") == [(1, "plugin:hello/greeter@1"), (2, "admin@varga.dev")]


async def test_an_upgrade_does_not_move_a_project_pin(client: AsyncClient, admin: str, offline: Path) -> None:
    from choregos_api.db.models import Agent, ProjectAgent
    from choregos_api.db.session import session_scope

    project = await make_project(client, "billing")
    await _install(client)
    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == "greeter"))).scalar_one()
        session.add(ProjectAgent(project_id=project["id"], org_id=agent.org_id, agent_id=agent.id, version=1))
    assert (await client.post(f"{ORG}/plugins/hello/upgrade", json={"version": "1.1.0"})).status_code == 200
    async with session_scope() as session:
        assert (await session.execute(select(ProjectAgent.version))).scalar_one() == 1


# ───────────────────────────── uninstall ─────────────────────────────


async def test_uninstall_removes_what_nobody_changed(client: AsyncClient, admin: str, offline: Path) -> None:
    before = await _counts()
    await _install(client)
    assert (await client.delete(f"{ORG}/plugins/hello")).status_code == 200
    after = await _counts()
    for table in ("agents", "agent_versions", "skills", "skill_versions", "org_plugins", "org_plugin_files"):
        assert after[table] == before[table], table
    assert (await client.get(f"{ORG}/plugins")).json() == {"items": []}
    assert (await client.delete(f"{ORG}/plugins/hello")).status_code == 404


async def test_uninstall_keeps_a_diverged_agent_and_says_so(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    from choregos_api.db.models import Agent, AuditLog, Skill
    from choregos_api.db.session import session_scope

    await _install(client)
    await _hand_version("greeter")
    assert (await client.delete(f"{ORG}/plugins/hello")).status_code == 200
    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == "greeter"))).scalar_one()
        assert agent.scope == "org", "kept, it is the organisation's now"
        # The skill it carries stays too: a kept agent still names it.
        assert (await session.execute(select(Skill.slug))).scalars().all() == ["greeting"]
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.action == "plugin.uninstall"))
        ).scalar_one()
        assert audit.payload["kept"] == {"agents": ["greeter"], "skills": ["greeting"]}


async def test_uninstall_keeps_an_agent_a_project_pins(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    from choregos_api.db.models import Agent, ProjectAgent
    from choregos_api.db.session import session_scope

    project = await make_project(client, "billing")
    await _install(client)
    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == "greeter"))).scalar_one()
        session.add(ProjectAgent(project_id=project["id"], org_id=agent.org_id, agent_id=agent.id, version=1))
    assert (await client.delete(f"{ORG}/plugins/hello")).status_code == 200
    assert await _versions("greeter") == [(1, "plugin:hello/greeter@1")]


# ───────────────────────────── templates, procedures, rights ─────────────────────────────


async def test_a_project_is_born_from_a_plugin_template_with_its_files(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope

    await _install(client)
    created = await client.post(
        f"{ORG}/projects",
        json={
            "slug": "greeted",
            "name": "Greeted",
            "template_ref": "plugin:hello/hello-flow",
            "config": {"slug": "greeted", "org": "varga"},
        },
    )
    assert created.status_code == 201, created.text
    async with session_scope() as session:
        names = (
            await session.execute(
                select(WorkflowDef.name).where(WorkflowDef.project_id == created.json()["id"])
            )
        ).scalars()
        assert "hello-simple" in set(names), "the workflow file of the plugin's template was published"
    refused = await client.post(
        f"{ORG}/projects",
        json={
            "slug": "nope",
            "name": "Nope",
            "template_ref": "plugin:hello/absent",
            "config": {"slug": "nope"},
        },
    )
    assert refused.status_code == 422


async def test_procedures_of_a_plugin_are_served_to_the_organisation_only(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    await _install(client)
    listed = {p["id"]: p for p in (await client.get("/api/v1/procedures")).json()}
    assert "connector.mcp.hello" in listed
    one = await client.get("/api/v1/procedures/connector.mcp.hello")
    assert one.status_code == 200 and one.json()["origin"] == "plugin:hello"
    await project_only(client, "stranger@elsewhere.dev")
    assert "connector.mcp.hello" not in {p["id"] for p in (await client.get("/api/v1/procedures")).json()}
    assert (await client.get("/api/v1/procedures/connector.mcp.hello")).status_code == 404


async def test_a_procedure_id_already_on_the_platform_is_a_conflict(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    from choregos_core import procedures

    shipped = sorted(procedures.load().procedures)[0]
    path = offline / "hello" / "1.0.0" / "procedures" / "connector.mcp.hello.md"
    path.write_text(path.read_text(encoding="utf-8").replace("id: connector.mcp.hello", f"id: {shipped}"))
    refused = await client.post(f"{ORG}/plugins/install", json=INSTALL)
    assert refused.status_code == 409 and shipped in refused.text


async def test_only_the_administrator_installs_and_members_read(
    client: AsyncClient, org: str, offline: Path
) -> None:
    await make_project(client, "billing")
    await add_member(client, "dev@varga.dev", "developer", "billing")
    await project_only(client, "dev@varga.dev")
    assert (await client.post(f"{ORG}/plugins/install", json=INSTALL)).status_code == 403
    assert (await client.get(f"{ORG}/plugins")).status_code == 200
    await as_user(client, "admin@varga.dev")
    await _install(client)
    await project_only(client, "dev@varga.dev")
    assert (await client.post(f"{ORG}/plugins/hello/upgrade")).status_code == 403
    assert (await client.delete(f"{ORG}/plugins/hello")).status_code == 403
    await project_only(client, "stranger@elsewhere.dev")
    assert (await client.get(f"{ORG}/plugins")).status_code == 403

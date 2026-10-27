"""Plugins through the deployment's sources, the second approval, default plugins (S43-05, ADR 0093).

- the routes obtain plugins through `fetch_plugin` once sources are configured (a lock mismatch is a
  422 naming the check: the offline directory of S43-04 had no lock);
- a plugin that brings a server is not installed on one administrator's word: 202, nothing written,
  and installed only when ANOTHER administrator approves, and only the content that was checked;
- uninstalling says what it kept;
- the start-up reconcile installs each default plugin in every organisation, twice is a no-op, a default
  plugin with a server is recorded as waiting and never auto-approved, a new organisation gets them;
- an unusable configuration stops the API at start-up.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import choregos_contracts as contracts
import pytest
import yaml
from httpx import AsyncClient
from jsonschema import Draft202012Validator
from sqlalchemy import func, select

from .agent_scopes_kit import add_member, as_user

ORG = "/api/v1/orgs/varga"
HELLO = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "hello"
INSTALL = {"source": "offline", "name": "hello", "version": "1.0.0"}
ADMIN = "admin@varga.dev"
SECOND = "second-admin@varga.dev"


def _edit(path: Path, change: Any) -> None:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _hello(target: Path, *, version: str = "1.0.0", server: bool = True) -> Path:
    shutil.copytree(HELLO, target)
    _edit(target / "plugin.yaml", lambda d: d["metadata"].update(version=version))
    if not server:
        shutil.rmtree(target / "servers")
        _edit(target / "plugin.yaml", lambda d: d["contents"].pop("servers"))
    return target


def _contract(name: str) -> Draft202012Validator:
    spec = contracts.load_openapi()
    return Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": spec["components"]})


@pytest.fixture
def configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Writes `CHOREGOS_PLUGINS_CONFIG` with an offline directory; returns that directory."""
    from choregos_api.config import reset_settings_cache

    def write(defaults: list[dict[str, str]] | None = None, *, server: bool = True) -> Path:
        offline = tmp_path / "offline"
        if not offline.exists():
            _hello(offline / "hello", server=server)
        path = tmp_path / "plugins.yaml"
        path.write_text(yaml.safe_dump({"offline": str(offline), "defaultPlugins": defaults or []}))
        monkeypatch.setenv("CHOREGOS_PLUGINS_CONFIG", str(path))
        reset_settings_cache()
        return offline

    yield write
    monkeypatch.delenv("CHOREGOS_PLUGINS_CONFIG", raising=False)
    reset_settings_cache()


async def _count(model: Any) -> int:
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def _counts() -> dict[str, int]:
    from choregos_api.db.models import Agent, AuditLog, OrgPlugin, OrgPluginApproval, OrgPluginFile, Skill

    return {
        m.__tablename__: await _count(m)
        for m in (Agent, Skill, OrgPlugin, OrgPluginFile, OrgPluginApproval, AuditLog)
    }


async def _second_admin(client: AsyncClient) -> None:
    await add_member(client, SECOND, "org_admin")
    await as_user(client, SECOND)


# ───────────────────────────── the sources serve the routes ─────────────────────────────


async def test_the_routes_obtain_plugins_through_the_sources_and_their_checks(
    client: AsyncClient, admin: str, configure: Any
) -> None:
    offline = configure(server=False)
    (offline / "plugins.lock").write_text(
        yaml.safe_dump({"plugins": {"hello": {"version": "1.0.0", "digest": "sha256:" + "0" * 64}}})
    )
    refused = await client.post(f"{ORG}/plugins/install", json=INSTALL)
    assert refused.status_code == 422 and "`lock`" in refused.text, refused.text
    (offline / "plugins.lock").unlink()
    assert (
        await client.post(f"{ORG}/plugins/install", json={**INSTALL, "source": "acme"})
    ).status_code == 404
    installed = await client.post(f"{ORG}/plugins/install", json={**INSTALL, "version": "^1"})
    assert installed.status_code == 201, installed.text
    assert not list(_contract("InstalledPlugin").iter_errors(installed.json()))
    assert installed.json()["version"] == "1.0.0" and installed.json()["contents"]["servers"] == []


# ───────────────────────────── the second approval ─────────────────────────────


async def test_a_plugin_that_brings_a_server_waits_for_a_second_administrator(
    client: AsyncClient, admin: str, configure: Any
) -> None:
    from choregos_api.db.models import OrgPlugin

    configure()
    before = await _counts()
    asked = await client.post(f"{ORG}/plugins/install", json=INSTALL)
    assert asked.status_code == 202, asked.text
    pending = asked.json()
    assert not list(_contract("PluginInstallPending").iter_errors(pending))
    assert "server `hello`" in pending["reason"]
    after = await _counts()
    for table in ("agents", "skills", "org_plugins", "org_plugin_files"):
        assert after[table] == before[table], f"{table}: nothing is installed before the approval"
    assert after["org_plugin_approvals"] == 1

    again = await client.post(f"{ORG}/plugins/install", json=INSTALL)
    assert again.status_code == 202 and again.json()["approval_id"] == pending["approval_id"]
    approve = f"{ORG}/plugin-approvals/{pending['approval_id']}/approve"
    own = await client.post(approve)
    assert own.status_code == 403 and "second administrator" in own.text
    assert await _count(OrgPlugin) == 0

    listed = (await client.get(f"{ORG}/plugin-approvals")).json()
    assert not list(_contract("PluginApprovalList").iter_errors(listed))
    assert [(a["status"], a["requested_by"], a["default"]) for a in listed["items"]] == [
        ("pending", ADMIN, False)
    ]

    await _second_admin(client)
    approved = await client.post(approve)
    assert approved.status_code == 200, approved.text
    assert not list(_contract("PluginApproval").iter_errors(approved.json()))
    assert (approved.json()["status"], approved.json()["decided_by"]) == ("approved", SECOND)
    plugins = (await client.get(f"{ORG}/plugins")).json()["items"]
    assert [(p["name"], p["installed_by"], p["contents"]["servers"]) for p in plugins] == [
        ("hello", ADMIN, ["hello"])
    ]
    assert (await client.post(approve)).status_code == 409, "approved once"


async def test_an_approval_installs_only_the_content_that_was_checked(
    client: AsyncClient, admin: str, configure: Any
) -> None:
    from choregos_api.db.models import OrgPlugin

    offline = configure()
    asked = await client.post(f"{ORG}/plugins/install", json=INSTALL)
    assert asked.status_code == 202, asked.text
    # The source now serves other bytes under the same version.
    _edit(
        offline / "hello" / "agents" / "greeter.yaml",
        lambda d: d["agent"]["spec"].update(instructions="Something else entirely, said once."),
    )
    await _second_admin(client)
    refused = await client.post(f"{ORG}/plugin-approvals/{asked.json()['approval_id']}/approve")
    assert refused.status_code == 409 and "was checked" in refused.text, refused.text
    assert await _count(OrgPlugin) == 0


async def test_an_upgrade_to_a_version_that_brings_a_server_waits_and_uninstall_drops_it(
    client: AsyncClient, admin: str, tmp_path: Path
) -> None:
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    _hello(root / "hello" / "1.0.0", server=False)
    _hello(root / "hello" / "1.1.0", version="1.1.0")
    use_plugin_supply(OfflineDirectory(root))
    try:
        assert (await client.post(f"{ORG}/plugins/install", json=INSTALL)).status_code == 201
        asked = await client.post(f"{ORG}/plugins/hello/upgrade", json={"version": "1.1.0"})
        assert asked.status_code == 202, asked.text
        assert [p["version"] for p in (await client.get(f"{ORG}/plugins")).json()["items"]] == ["1.0.0"]
        await _second_admin(client)
        approved = await client.post(f"{ORG}/plugin-approvals/{asked.json()['approval_id']}/approve")
        assert approved.status_code == 200, approved.text
        assert [p["version"] for p in (await client.get(f"{ORG}/plugins")).json()["items"]] == ["1.1.0"]

        # A request still waiting goes with the plugin.
        _hello(root / "hello" / "1.2.0", version="1.2.0")
        assert (await client.post(f"{ORG}/plugins/hello/upgrade")).status_code == 202
        uninstalled = await client.delete(f"{ORG}/plugins/hello")
        assert uninstalled.status_code == 200, uninstalled.text
        assert not list(_contract("PluginUninstallResult").iter_errors(uninstalled.json()))
        statuses = [a["status"] for a in (await client.get(f"{ORG}/plugin-approvals")).json()["items"]]
        assert statuses == ["approved"]
    finally:
        use_plugin_supply(None)


async def test_uninstall_answers_what_it_removed_and_what_it_kept(
    client: AsyncClient, admin: str, configure: Any
) -> None:
    from .test_plugin_installer import _hand_version

    configure(server=False)
    assert (await client.post(f"{ORG}/plugins/install", json=INSTALL)).status_code == 201
    await _hand_version("greeter")
    uninstalled = await client.delete(f"{ORG}/plugins/hello")
    assert uninstalled.status_code == 200, uninstalled.text
    body = uninstalled.json()
    assert not list(_contract("PluginUninstallResult").iter_errors(body))
    assert body == {
        "name": "hello",
        "version": "1.0.0",
        "removed": {"agents": [], "skills": []},
        "kept": {"agents": ["greeter"], "skills": ["greeting"]},
    }


# ───────────────────────────── default plugins ─────────────────────────────

DEFAULT = [{"source": "offline", "name": "hello", "version": "^1"}]


async def test_the_reconcile_installs_each_default_plugin_once(
    client: AsyncClient, admin: str, configure: Any
) -> None:
    from choregos_api.db.models import OrgPlugin
    from choregos_api.db.session import session_scope
    from choregos_api.services import plugin_defaults

    configure(DEFAULT, server=False)
    first = await plugin_defaults.reconcile_all()
    assert [(o.org, o.plugin, o.outcome) for o in first] == [("varga", "hello", "installed")]
    async with session_scope() as session:
        row = (await session.execute(select(OrgPlugin))).scalar_one()
        assert (row.is_default, row.installed_by, row.version) == (True, None, "1.0.0")
    before = await _counts()
    second = await plugin_defaults.reconcile_all()
    assert [o.outcome for o in second] == ["present"]
    assert await _counts() == before, "a second reconcile writes nothing, not even an audit entry"
    listed = (await client.get(f"{ORG}/plugins")).json()["items"]
    assert [(p["name"], p["default"], p["installed_by"]) for p in listed] == [("hello", True, None)]


async def test_a_default_plugin_that_brings_a_server_waits_and_is_never_approved_by_the_platform(
    client: AsyncClient, admin: str, configure: Any
) -> None:
    from choregos_api.db.models import OrgPlugin, OrgPluginApproval
    from choregos_api.services import plugin_defaults

    offline = configure(DEFAULT)
    first = await plugin_defaults.reconcile_all()
    assert [(o.plugin, o.outcome) for o in first] == [("hello", "pending_approval")]
    assert await _count(OrgPlugin) == 0
    # A request already waiting is not fetched again: the second pass never reaches the source.
    shutil.move(offline / "hello", offline.parent / "hello-aside")
    second = await plugin_defaults.reconcile_all()
    shutil.move(offline.parent / "hello-aside", offline / "hello")
    assert [o.outcome for o in second] == ["pending_approval"]
    assert await _count(OrgPluginApproval) == 1, "asked once"

    (waiting,) = (await client.get(f"{ORG}/plugin-approvals")).json()["items"]
    assert (waiting["requested_by"], waiting["default"], waiting["status"]) == (None, True, "pending")
    # The platform asked: the organisation's administrator is the second person.
    approved = await client.post(f"{ORG}/plugin-approvals/{waiting['id']}/approve")
    assert approved.status_code == 200, approved.text
    listed = (await client.get(f"{ORG}/plugins")).json()["items"]
    assert [(p["name"], p["default"], p["installed_by"]) for p in listed] == [("hello", True, None)]
    assert [o.outcome for o in await plugin_defaults.reconcile_all()] == ["present"]


async def test_a_default_plugin_that_fails_does_not_stop_the_others(
    client: AsyncClient, admin: str, configure: Any
) -> None:
    from choregos_api.services import plugin_defaults

    configure([{"source": "offline", "name": "absent", "version": "*"}, *DEFAULT], server=False)
    outcomes = await plugin_defaults.reconcile_all()
    assert [(o.plugin, o.outcome) for o in outcomes] == [("absent", "failed"), ("hello", "installed")]


async def test_a_new_organisation_gets_the_default_plugins(
    client: AsyncClient, admin: str, org: str, configure: Any
) -> None:
    from choregos_api import edition

    configure(DEFAULT, server=False)
    edition.declarer(edition.ENTREPRISE, fonctions=frozenset({"multi-org"}))
    try:
        created = await client.post("/api/v1/orgs", json={"slug": "second", "name": "Second"})
        assert created.status_code == 201, created.text
    finally:
        edition.reinitialiser()
    listed = (await client.get("/api/v1/orgs/second/plugins")).json()["items"]
    assert [(p["name"], p["default"]) for p in listed] == [("hello", True)]
    assert (await client.get(f"{ORG}/plugins")).json()["items"] == [], "the other one waits for start-up"


# ───────────────────────────── the configuration, at start-up ─────────────────────────────


def test_an_unusable_configuration_stops_the_api_at_start_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.main import create_app
    from choregos_api.services.plugin_defaults import PluginsConfigurationError, check_configuration

    path = tmp_path / "plugins.yaml"
    path.write_text(yaml.safe_dump({"sources": [{"name": "acme", "index": "https://a.example/i.yaml"}]}))
    monkeypatch.setenv("CHOREGOS_PLUGINS_CONFIG", str(path))
    reset_settings_cache()
    try:
        with pytest.raises(PluginsConfigurationError, match="CHOREGOS_PLUGINS_CONFIG"):
            create_app()

        path.write_text(yaml.safe_dump({"offline": str(tmp_path / "missing")}))
        reset_settings_cache()
        with pytest.raises(PluginsConfigurationError, match="does not exist"):
            check_configuration()

        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        path.write_text(yaml.safe_dump({"offline": str(tmp_path / "a")}))
        monkeypatch.setenv("CHOREGOS_PLUGINS_OFFLINE", str(tmp_path / "b"))
        reset_settings_cache()
        with pytest.raises(PluginsConfigurationError, match="two offline plugin directories"):
            check_configuration()

        monkeypatch.setenv("CHOREGOS_PLUGINS_OFFLINE", str(tmp_path / "a"))
        reset_settings_cache()
        assert check_configuration().offline == tmp_path / "a"
    finally:
        monkeypatch.delenv("CHOREGOS_PLUGINS_CONFIG")
        monkeypatch.delenv("CHOREGOS_PLUGINS_OFFLINE", raising=False)
        reset_settings_cache()

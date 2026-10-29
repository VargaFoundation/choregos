"""The built-in agents leave the core (S43-09, ADR 0093 §6, plan 2026-10-27 Decision 6).

- the API reads the archives of `plugins/offline/` against `plugins.lock` (`tests/tools/` checks the lock);
- a default plugin the gate sends to a second administrator waits — unless the deployment's
  configuration pre-approved it, and then the audit trail says who approved it: the configuration.
  The API never takes that flag;
- a default plugin of a declared source is taken from the offline directory when it holds a version in
  range: no network, and the row keeps the declared source for its upgrades;
- the installer keeps an external client's `client` and `reach`, and the agent files the organisation's
  catalogue then reads: an installed version of a plugin replaces the offline one in the catalogue;
- the agent-catalogue routes keep working on top of plugins.
"""

from __future__ import annotations

import tarfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import AsyncClient
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[3]
OFFLINE = ROOT / "plugins" / "offline"
ORG = "/api/v1/orgs/varga"
VARGA = {
    "name": "varga",
    "index": "oci://ghcr.io/vargafoundation/choregos-agents/index:latest",
    "trust": {
        "identity": r"https://github\.com/VargaFoundation/choregos-agents/\.github/workflows/publish\.yml@refs/heads/main",
        "issuer": r"https://token\.actions\.githubusercontent\.com",
    },
}


# ───────────────────────────── the lock ─────────────────────────────


def test_the_api_reads_the_offline_directory_against_its_lock() -> None:
    from choregos_core.plugins.sources import offline_index

    index = offline_index(OFFLINE)
    assert {name: sorted(v) for name, v in index.releases.items()} == {
        "authoring": ["1.0.0"],
        "mcp-clients": ["1.0.0"],
        "netops": ["1.0.0"],
        "requests": ["1.0.1"],
        "software-delivery": ["1.0.0"],
    }


# ───────────────────────────── default plugins ─────────────────────────────


@pytest.fixture
def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The chart's configuration: the `varga` source, the offline directory, these defaults; the core
    reports the release that installs plugins (these plugins require `>=0.21.0`)."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.services import plugin_sources
    from choregos_core.plugins import versions

    fake = plugin_sources.FakePluginSource()
    plugin_sources.set_backend(fake)
    monkeypatch.setattr(plugin_sources, "core_version", lambda: "0.21.0")
    monkeypatch.setattr(versions, "core_version", lambda: "0.21.0")

    def write(defaults: list[dict[str, Any]], offline: Path = OFFLINE) -> Any:
        path = tmp_path / "plugins.yaml"
        path.write_text(
            yaml.safe_dump({"sources": [VARGA], "defaultPlugins": defaults, "offline": str(offline)})
        )
        monkeypatch.setenv("CHOREGOS_PLUGINS_CONFIG", str(path))
        reset_settings_cache()
        return fake

    yield write
    plugin_sources.set_backend(None)
    monkeypatch.delenv("CHOREGOS_PLUGINS_CONFIG", raising=False)
    reset_settings_cache()


async def _rows() -> dict[str, Any]:
    from choregos_api.db.models import OrgPlugin
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return {r.name: r for r in (await session.execute(select(OrgPlugin))).scalars()}


async def _audit(action: str) -> list[Any]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars())


async def test_software_delivery_waits_for_a_second_administrator_without_preapproval(
    client: AsyncClient, admin: str, deployment: Any
) -> None:
    from choregos_api.services import plugin_defaults

    deployment([{"source": "varga", "name": "software-delivery", "version": "^1.0"}])
    outcomes = await plugin_defaults.reconcile_all()
    assert [(o.plugin, o.outcome) for o in outcomes] == [("software-delivery", "pending_approval")]
    assert "madr-4" in outcomes[0].detail and "high risk" in outcomes[0].detail
    assert await _rows() == {}
    assert await _audit("plugin.default.preapproved") == []


async def test_a_preapproved_default_is_installed_and_the_configuration_is_the_approver(
    client: AsyncClient, admin: str, deployment: Any
) -> None:
    from choregos_api.services import plugin_defaults

    fake = deployment(
        [{"source": "varga", "name": "software-delivery", "version": "^1.0", "preapproved": True}]
    )
    outcomes = await plugin_defaults.reconcile_all()
    assert [(o.plugin, o.outcome) for o in outcomes] == [("software-delivery", "installed")]
    assert fake.calls == [], "taken from the offline directory: no network at start-up"
    row = (await _rows())["software-delivery"]
    assert (row.source, row.version, row.is_default, row.installed_by) == ("varga", "1.0.0", True, None)
    (approval,) = await _audit("plugin.default.preapproved")
    assert approval.payload["approved_by"] == "deployment configuration"
    assert any("madr-4" in reason for reason in approval.payload["reasons"])
    architect = (await client.get(f"{ORG}/agents/architect")).json()
    assert architect["versions"][0]["created_by"] == "plugin:software-delivery/architect@2"
    assert (await client.get(f"{ORG}/plugin-approvals")).json()["items"] == []


async def test_requests_is_installed_without_a_second_administrator(
    client: AsyncClient, admin: str, deployment: Any
) -> None:
    """The agents of `default-request` (ADR 0110, #1091): the chart lists `requests` without
    `preapproved`, so the gate must find nothing in it for a second administrator."""
    from choregos_api.services import plugin_defaults

    fake = deployment([{"source": "varga", "name": "requests", "version": "^1.0"}])
    outcomes = await plugin_defaults.reconcile_all()
    assert [(o.plugin, o.outcome) for o in outcomes] == [("requests", "installed")]
    assert fake.calls == [], "taken from the offline directory: no network at start-up"
    assert (await _rows())["requests"].version == "1.0.1"
    assert await _audit("plugin.default.preapproved") == []
    for slug in ("brief-writer", "assistant"):
        agent = (await client.get(f"{ORG}/agents/{slug}")).json()
        assert agent["versions"][0]["created_by"] == f"plugin:requests/{slug}@1", agent


async def test_the_api_never_takes_a_preapproval(client: AsyncClient, admin: str, deployment: Any) -> None:
    deployment([])
    refused = await client.post(
        f"{ORG}/plugins/install",
        json={"source": "offline", "name": "software-delivery", "version": "1.0.0", "preapproved": True},
    )
    assert refused.status_code == 422, refused.text
    waiting = await client.post(
        f"{ORG}/plugins/install", json={"source": "offline", "name": "software-delivery", "version": "1.0.0"}
    )
    assert waiting.status_code == 202, waiting.text


async def test_a_default_outside_the_offline_versions_goes_to_its_source(
    client: AsyncClient, admin: str, deployment: Any
) -> None:
    from choregos_api.services import plugin_defaults

    fake = deployment([{"source": "varga", "name": "netops", "version": "^2.0", "preapproved": True}])
    outcomes = await plugin_defaults.reconcile_all()
    assert [(o.plugin, o.outcome) for o in outcomes] == [("netops", "failed")]
    assert fake.calls, "the declared source was asked"


# ───────────────────────────── the installer and the catalogue view ─────────────────────────────


async def test_the_installer_keeps_the_clients_client_and_reach(
    client: AsyncClient, admin: str, deployment: Any
) -> None:
    deployment([])
    installed = await client.post(
        f"{ORG}/plugins/install", json={"source": "offline", "name": "mcp-clients", "version": "1.0.0"}
    )
    assert installed.status_code == 201, installed.text
    clients = (await _rows())["mcp-clients"].details["clients"]
    assert clients["claude-ai"] == {"client": "claude-ai", "reach": "cloud"}
    assert clients["copilot-vscode"] == {"client": "vscode", "reach": "always"}
    listed = {e["slug"]: e for e in (await client.get(f"{ORG}/agent-catalogue")).json()}
    assert (listed["chatgpt"]["client"], listed["chatgpt"]["reach"]) == ("chatgpt", "cloud")
    assert listed["claude-code"]["installed"] is True and listed["claude-code"]["own_agent"] is False


async def test_an_installed_version_replaces_the_offline_one_in_the_catalogue(
    client: AsyncClient, admin: str, tmp_path: Path, deployment: Any
) -> None:
    offline = tmp_path / "offline"
    offline.mkdir()
    with tarfile.open(OFFLINE / "netops-1.0.0.tar.gz") as source:
        source.extractall(offline / "netops", filter="data")
    manifest = offline / "netops" / "plugin.yaml"
    document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    document["metadata"]["version"] = "1.1.0"
    manifest.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    agent = offline / "netops" / "agents" / "netops.yaml"
    entry = yaml.safe_load(agent.read_text(encoding="utf-8"))
    entry["catalogue"]["version"] = 3
    agent.write_text(yaml.safe_dump(entry, sort_keys=False), encoding="utf-8")
    deployment([], offline)
    installed = await client.post(
        f"{ORG}/plugins/install", json={"source": "offline", "name": "netops", "version": "1.1.0"}
    )
    assert installed.status_code == 201, installed.text

    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.catalogue_d_agents import catalogue_of

    # The deployment's directory no longer holds it: the organisation's catalogue still does.
    deployment([], tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    async with session_scope() as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
        view = await catalogue_of(session, org_id)
    found = view.entry("netops")
    assert found is not None and (found.version, found.plugin_version) == (3, "1.1.0")
    assert found.provenance == "plugin:netops/netops@3"


async def test_without_the_authoring_plugin_the_drafter_says_what_to_install(
    client: AsyncClient, admin: str, tmp_path: Path, deployment: Any
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.agent_drafts import DraftingError, instructions_for_drafter

    (tmp_path / "empty").mkdir()
    deployment([], tmp_path / "empty")
    async with session_scope() as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
        with pytest.raises(DraftingError, match="plugin `authoring`"):
            await instructions_for_drafter(session, org_id)

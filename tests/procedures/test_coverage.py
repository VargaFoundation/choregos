# SPDX-License-Identifier: Apache-2.0
"""A type ships with its setup procedure, or this suite is red (ADR 0067).

Every connector type the platform ships, every knowledge source kind, every client of the MCP door's
page and the external-agent kinds known to this checkout must have a procedure. Add a type without
one and the first test below names it.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Iterator

import choregos_adapters
import pytest
import yaml
from choregos_adapters import registry
from choregos_api.knowledge.sync import KINDS as SOURCE_KINDS
from choregos_core import procedures as core

ROOT = pathlib.Path(__file__).resolve().parents[2]
CATALOGUE = core.load()
#: The catalogue's client slug, when the console's page names it differently.
DOOR_ALIASES = {"copilot-vscode": "vscode"}
#: The configuration keys of a knowledge source (`KnowledgeSourceCreate.config`, per kind).
SOURCE_FIELDS = {
    "sharepoint": {"connector", "site_id", "drive_id", "folder"},
    "confluence": {"connector", "space_key", "page_id", "include_root", "include_attachments", "labels"},
    "web": {"url", "max_depth", "max_pages"},
    "git": {"connector", "url", "ref", "paths"},
}


def connector_targets() -> list[str]:
    """Every shipped `kind/type`, deprecated aliases included, fakes (test doubles) excluded."""
    return sorted(f"{kind}/{name}" for (kind, name) in choregos_adapters._SPECS_LIVREES if name != "fake")


def missing_connector_procedures(targets: list[str], catalogue: core.Catalogue = CATALOGUE) -> list[str]:
    return [t for t in targets if catalogue.for_target(f"connector:{t}") is None]


def door_clients() -> list[str]:
    """The `id`s of `CLIENTS` in the console's integrations page."""
    text = (ROOT / "apps/web/src/lib/integrations.ts").read_text(encoding="utf-8")
    block = text.split("export const CLIENTS", 1)[1].split("];", 1)[0]
    return re.findall(r'\bid: "([a-z-]+)"', block)


def test_every_shipped_connector_type_has_a_procedure() -> None:
    assert len(connector_targets()) >= 32
    assert missing_connector_procedures(connector_targets()) == []


def test_a_new_type_without_a_procedure_is_caught() -> None:
    """The coverage check itself is red when a type ships alone."""
    registry.register("tracker", "temporary-demo", registry.ConnectorTypeSpec("temporary", ("tracker",)))(
        lambda config: object()
    )
    try:
        # Other tests register types of their own and may leave them: look at the shipped ones and ours.
        known = sorted(f"{k}/{t}" for k, t, _ in registry.connector_types())
        ours = [t for t in known if t in set(connector_targets()) or t == "tracker/temporary-demo"]
        assert "tracker/temporary-demo" in ours
        assert missing_connector_procedures(ours) == ["tracker/temporary-demo"]
    finally:
        registry._SPECS.pop(("tracker", "temporary-demo"), None)
        registry._REGISTRY.pop(("tracker", "temporary-demo"), None)


@pytest.mark.parametrize("kind", SOURCE_KINDS)
def test_every_knowledge_source_kind_has_a_procedure(kind: str) -> None:
    proc = CATALOGUE.for_target(f"source:{kind}")
    assert proc is not None, f"source kind `{kind}` has no procedure"
    assert proc.id == f"source.{kind}"


def test_every_door_client_of_the_integrations_page_has_a_procedure_that_links_to_it() -> None:
    clients = door_clients()
    assert len(clients) >= 9
    for client in clients:
        proc = CATALOGUE.for_target(f"door_client:{client}")
        assert proc is not None, f"client `{client}` has no procedure"
        assert proc.front.link == f"/integrations/{client}"


def test_every_agent_of_the_client_catalogue_is_covered() -> None:
    folder = ROOT / "packages/core/src/choregos_core/catalogue_d_agents/clients"
    slugs = [
        yaml.safe_load(p.read_text(encoding="utf-8"))["catalogue"]["client"] for p in folder.glob("*.yaml")
    ]
    assert slugs
    for slug in slugs:
        client = DOOR_ALIASES.get(slug, slug)
        assert CATALOGUE.for_target(f"door_client:{client}") is not None, f"`{slug}` has no procedure"


def test_the_procedures_of_the_batch_four_types_exist() -> None:
    """Remote agents (generic A2A and Atlassian Rovo by card host), code agents and the git connector."""
    generic = CATALOGUE.for_target("agent:remote.a2a")
    rovo = CATALOGUE.for_target("agent:remote.a2a", card_host="a2a.atlassian.com")
    other = CATALOGUE.for_target("agent:remote.a2a", card_host="agents.example.com")
    assert generic is not None and rovo is not None and other is not None
    assert (generic.id, rovo.id, other.id) == (
        "agent.remote.a2a",
        "agent.remote.a2a.atlassian-rovo",
        "agent.remote.a2a",
    )
    assert CATALOGUE.for_target("agent:code") is not None
    assert CATALOGUE.for_target("connector:documents/git") is not None


def test_the_rovo_a2a_procedure_names_the_skills_of_the_agent_card_fixture() -> None:
    card = json.loads(
        (ROOT / "packages/adapters/tests/fixtures/a2a/rovo-agent-card.json").read_text(encoding="utf-8")
    )
    skills = {s["id"] for s in card["skills"]}
    body = CATALOGUE.procedures["agent.remote.a2a.atlassian-rovo"].body
    assert skills and all(f"`{skill}`" in body for skill in skills), skills


def test_the_rovo_mcp_preset_is_found_by_its_name_and_the_generic_one_otherwise() -> None:
    preset = CATALOGUE.for_target("connector:mcp/mcp", preset="atlassian-rovo")
    generic = CATALOGUE.for_target("connector:mcp/mcp")
    assert preset is not None and generic is not None
    assert (preset.id, generic.id) == ("connector.mcp.mcp.atlassian-rovo", "connector.mcp.mcp")


def _connector_procedures() -> Iterator[core.Procedure]:
    return (p for p in CATALOGUE.procedures.values() if p.target[0] == "connector")


@pytest.mark.parametrize("proc", list(_connector_procedures()), ids=lambda p: p.id)
def test_a_connector_procedure_talks_only_about_fields_its_type_has(proc: core.Procedure) -> None:
    kind, _, name = proc.target[1].partition("/")
    spec = registry.spec_of(kind, name)
    assert spec is not None, f"{proc.id} applies to a type that is not registered"
    known = set(spec.config_schema.get("properties", {})) | set(spec.secret_fields)
    assert set(proc.front.fields) <= known, sorted(set(proc.front.fields) - known)
    # A secret field the form asks for must be explained.
    assert set(spec.secret_fields) <= set(proc.front.fields), (
        f"{proc.id} does not say what to paste in {sorted(set(spec.secret_fields) - set(proc.front.fields))}"
    )


@pytest.mark.parametrize("kind", SOURCE_KINDS)
def test_a_source_procedure_talks_only_about_the_fields_of_its_kind(kind: str) -> None:
    proc = CATALOGUE.procedures[f"source.{kind}"]
    assert set(proc.front.fields) <= SOURCE_FIELDS[kind]


def test_a_procedure_for_a_secret_field_never_asks_for_an_environment_reference() -> None:
    """Secrets are typed in the console (ADR 0058); an `env:` reference is an operator's detour."""
    for proc in CATALOGUE.procedures.values():
        assert "env:" not in proc.body, proc.id

"""The workflow catalogue (S49-05 to S49-07, ADR 0111): list, adopt, share."""

from __future__ import annotations

from typing import Any

from choregos_api.services.workflow_catalogue import renamed
from choregos_core.dsl import template_yaml
from httpx import AsyncClient

from .conftest import login

BASE = "/api/v1/orgs/varga/workflow-catalogue"

GHOST = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: ghostly, version: 1, catalogue: { title: Ghostly, tags: [demo] } }
actors:
  spirit: { type: agent, role: refine, agent: ghost-agent }
  owner: { type: human, group: product-owners }
states:
  inbox: { display: Inbox, kind: wait }
  done: { display: Done, terminal: true }
transitions:
  - { id: t-haunt, from: inbox, to: done, by: spirit, outputs: [spec_markdown], gates: [outputs_present] }
"""


def _entry(entries: list[dict[str, Any]], source: str, name: str) -> dict[str, Any]:
    return next(e for e in entries if e["source"] == source and e["name"] == name)


async def test_the_catalogue_lists_core_and_template_workflows_with_what_they_need(
    client: AsyncClient, admin: str
) -> None:
    listed = await client.get(BASE)
    assert listed.status_code == 200, listed.text
    entries = listed.json()
    request = _entry(entries, "core", "default-request")
    assert request["needs_repository"] is False
    assert [r["capability"] for r in request["requires"]] == ["tracker", "runtime", "gateway"]
    assert request["groups"] == ["product-owners"]
    assert request["agents"] == [], "its two agents are core playbooks (ADR 0110), not registry agents"
    delivery = _entry(entries, "template:github-software-delivery", "dev-simple")
    assert ("developer", "catalogue") in {(a["slug"], a["resolution"]) for a in delivery["agents"]}, (
        "not installed yet: the platform's catalogue (its offline plugins) provides it"
    )
    assert _entry(entries, "core", "default-simple")["needs_repository"] is True
    hr = [e for e in entries if e["source"] == "template:joiners-leavers"]
    assert hr and all(not e["needs_repository"] for e in hr), "a template's own workflows are listed"
    assert not any(e["source"].startswith("template:") and e["name"] == "default-simple" for e in entries), (
        "a template's reference to a core workflow is the core entry, not a second one"
    )


async def test_the_list_leaves_every_entry_s_yaml_out(client: AsyncClient, admin: str) -> None:
    """S55-04b: each entry's whole text made the library slow to load, and no page reads it."""
    entries = (await client.get(BASE)).json()
    assert entries, "the catalogue lists nothing: the test checks nothing"
    assert all(entry["yaml"] is None for entry in entries)


async def test_a_member_reads_the_catalogue_an_outsider_does_not(client: AsyncClient, admin: str) -> None:
    await client.post("/api/v1/orgs/varga/members", json={"email": "po@varga.dev", "role": "project_owner"})
    await login(client, "po@varga.dev")
    assert (await client.get(BASE)).status_code == 200
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(Organization(slug="acme", name="Acme"))
    assert (await client.get("/api/v1/orgs/acme/workflow-catalogue")).status_code == 403, (
        "not a member of acme"
    )


async def test_adopting_publishes_the_entry_installs_its_agents_and_records_its_origin(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    adopted = await client.post(
        f"/api/v1/projects/{pid}/workflow-adoptions",
        json={"source": "core", "name": "default-request", "as": "requests", "make_default": True},
    )
    assert adopted.status_code == 201, adopted.text
    row = adopted.json()
    assert (row["name"], row["source"], row["origin"]) == ("requests", "catalogue", "core/default-request@1")
    assert "name: requests" in row["yaml"] and "# The default workflow" in row["yaml"], (
        "renamed, comments kept"
    )
    assert row["is_default"] is True
    routing = (await client.get(f"/api/v1/projects/{pid}/workflow-routing")).json()
    assert routing["default"] == "requests"
    delivery = await client.post(
        f"/api/v1/projects/{pid}/workflow-adoptions",
        json={"source": "template:github-software-delivery", "name": "dev-simple"},
    )
    assert delivery.status_code == 201, delivery.text
    adopted_entry = _entry((await client.get(BASE)).json(), "template:github-software-delivery", "dev-simple")
    assert {a["resolution"] for a in adopted_entry["agents"]} == {"plugin"}, (
        "installed by the adoption, from their plugin"
    )

    again = await client.post(
        f"/api/v1/projects/{pid}/workflow-adoptions",
        json={"source": "core", "name": "default-request", "as": "requests"},
    )
    assert again.status_code == 201 and again.json()["version"] == row["version"] + 1, "an update"
    taken = await client.post(
        f"/api/v1/projects/{pid}/workflow-adoptions",
        json={"source": "core", "name": "default-request", "as": "default-simple"},
    )
    assert taken.status_code == 409, "a name used by another workflow is never overwritten"
    stale = await client.post(
        f"/api/v1/projects/{pid}/workflow-adoptions",
        json={"source": "core", "name": "default-request", "version": 7, "as": "other"},
    )
    assert stale.status_code == 409
    unknown = await client.post(
        f"/api/v1/projects/{pid}/workflow-adoptions", json={"source": "core", "name": "nope"}
    )
    assert unknown.status_code == 404


async def test_sharing_copies_a_version_and_an_unresolved_agent_refuses_the_adoption(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    put = await client.put(
        f"/api/v1/projects/{pid}/workflows/ghostly", json={"yaml": GHOST, "create_only": True}
    )
    assert put.status_code == 200, put.text
    shared = await client.post(BASE, json={"project": "billing-api", "name": "ghostly"})
    assert shared.status_code == 201, shared.text
    entry = shared.json()
    assert (entry["source"], entry["version"], entry["shared_from"]) == ("org", 1, "billing-api/ghostly@1")
    assert entry["title"] == "Ghostly" and entry["tags"] == ["demo"]
    assert [(a["slug"], a["resolution"]) for a in entry["agents"]] == [("ghost-agent", "unresolved")]

    edited = GHOST.replace("display: Done", "display: Finished")
    assert (
        await client.put(f"/api/v1/projects/{pid}/workflows/ghostly", json={"yaml": edited})
    ).status_code == 200
    assert "display: Done" in entry["yaml"], "the share's answer carries the copy it made"
    listed = _entry((await client.get(BASE)).json(), "org", "ghostly")
    assert listed["version"] == 1, "a share is a copy: later edits do not reach it"
    assert listed["yaml"] is None, "the list carries no YAML (S55-04b)"
    second = await client.post(BASE, json={"project": "billing-api", "name": "ghostly"})
    assert second.json()["version"] == 2 and "Finished" in second.json()["yaml"]

    refused = await client.post(
        f"/api/v1/projects/{pid}/workflow-adoptions",
        json={"source": "org", "name": "ghostly", "as": "haunted"},
    )
    assert refused.status_code == 422 and "ghost-agent" in refused.text and "never installs" in refused.text


async def test_only_an_administrator_shares(client: AsyncClient, project: dict[str, Any]) -> None:
    listed = (await client.get(f"/api/v1/projects/{project['id']}/workflows")).json()
    assert all(w["can_share"] for w in listed), "the server says an org_admin may share"
    await client.post("/api/v1/orgs/varga/members", json={"email": "po@varga.dev", "role": "project_owner"})
    await login(client, "po@varga.dev")
    refused = await client.post(BASE, json={"project": "billing-api", "name": "default-simple"})
    assert refused.status_code == 403
    listed = (await client.get(f"/api/v1/projects/{project['id']}/workflows")).json()
    assert not any(w["can_share"] for w in listed), "and a project owner sees no share gesture"


def test_renaming_keeps_every_other_byte() -> None:
    source = template_yaml("default-request")
    out = renamed(source, "requests")
    assert out.replace("name: requests", "name: default-request", 1) == source


async def test_the_core_catalogue_offers_development_and_audit_workflows_with_their_agents(
    client: AsyncClient, project: dict[str, Any], admin: str
) -> None:
    """S59-02: the core ships a catalogue (development, audit, decision, documentation, operations), each
    entry presented and its agents resolved; adopting an audit installs the agents it names."""
    entries = (await client.get(BASE)).json()
    core = {e["name"]: e for e in entries if e["source"] == "core"}
    assert {"dev-feature", "dev-fix", "hotfix", "code-audit", "security-audit", "incident-postmortem"} <= set(
        core
    )
    audit = core["security-audit"]
    assert audit["title"] == "Security audit" and "audit" in audit["tags"]
    assert {a["slug"] for a in audit["agents"]} == {"secops", "security-reviewer", "compliance"}
    assert "unresolved" not in {a["resolution"] for a in audit["agents"]}, "every agent ships with the core"

    adopted = await client.post(
        f"/api/v1/projects/{project['id']}/workflow-adoptions",
        json={"source": "core", "name": "security-audit"},
    )
    assert adopted.status_code == 201, adopted.text
    assert adopted.json()["origin"] == "core/security-audit@1"
    agents = (await client.get("/api/v1/orgs/varga/agents")).json()
    listed = {a["slug"] for a in (agents["items"] if isinstance(agents, dict) else agents)}
    assert {"secops", "security-reviewer", "compliance"} <= listed, "the adoption installed its agents"

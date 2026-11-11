"""An organisation may require complete agents (S60-05, ADR 0127 §8): its `complete_agents` setting.

Switched by its administrator only. While it is not set, a version that leaves its model, a tool source or
its memories to its project is published as before. Once it is `required`, such a version is a 422
`agent_incomplete` naming each component, at creation and at every new version; a complete one, an external
agent and a restore of an accepted version are published. `null` lifts the requirement.
"""

from __future__ import annotations

from typing import Any

from choregos_core.agent_completeness import ORG_SETTING, org_requires_complete
from httpx import AsyncClient

from .conftest import login

ORG = "/api/v1/orgs"
AGENTS = f"{ORG}/varga/agents"
INCOMPLETE = {"instructions": "Answer the question.", "model": "m"}
COMPLETE = {
    **INCOMPLETE,
    "tools": {"catalogue": [], "extensions": [], "web": []},
    "memories": [],
}


async def _stored() -> dict[str, Any]:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        organisation = (
            await session.execute(select(Organization).where(Organization.slug == "varga"))
        ).scalar_one()
        return dict(organisation.settings or {})


async def _require(client: AsyncClient, value: str | None) -> None:
    switched = await client.patch(f"{ORG}/varga", json={"complete_agents": value})
    assert switched.status_code == 200, switched.text
    assert switched.json()["complete_agents"] == value


def _missing(response: Any) -> list[list[str]]:
    errors = response.json()["errors"]
    assert {e["code"] for e in errors} == {"agent_incomplete"}
    return [e["loc"] for e in errors]


async def test_an_incomplete_version_is_published_until_the_organisation_requires_complete_agents(
    client: AsyncClient, admin: str
) -> None:
    assert not org_requires_complete(await _stored())
    created = await client.post(AGENTS, json={"slug": "loose", "display_name": "L", "spec": INCOMPLETE})
    assert created.status_code == 201, created.text


async def test_a_required_organisation_refuses_an_incomplete_agent_and_names_what_it_leaves_out(
    client: AsyncClient, admin: str
) -> None:
    await _require(client, "required")
    assert (await _stored())[ORG_SETTING] == "required"
    refused = await client.post(
        AGENTS, json={"slug": "loose", "display_name": "L", "spec": {"instructions": "Answer."}}
    )
    assert refused.status_code == 422, refused.text
    assert _missing(refused) == [
        ["spec", "model"],
        ["spec", "tools", "catalogue"],
        ["spec", "tools", "extensions"],
        ["spec", "tools", "web"],
        ["spec", "memories"],
    ]
    created = await client.post(AGENTS, json={"slug": "closed", "display_name": "C", "spec": COMPLETE})
    assert created.status_code == 201, created.text


async def test_every_new_version_is_judged_and_a_restore_is_not(client: AsyncClient, admin: str) -> None:
    created = await client.post(AGENTS, json={"slug": "legacy", "display_name": "L", "spec": INCOMPLETE})
    assert created.status_code == 201, created.text
    await _require(client, "required")
    refused = await client.post(f"{AGENTS}/legacy/versions", json={**INCOMPLETE, "instructions": "Again."})
    assert refused.status_code == 422, refused.text
    assert _missing(refused) == [
        ["spec", "tools", "catalogue"],
        ["spec", "tools", "extensions"],
        ["spec", "tools", "web"],
        ["spec", "memories"],
    ]
    completed = await client.post(f"{AGENTS}/legacy/versions", json=COMPLETE)
    assert completed.status_code in {200, 201}, completed.text
    restored = await client.post(f"{AGENTS}/legacy/versions/1/restore", json={"base_version": 2})
    assert restored.status_code in {200, 201}, "a restore reproduces a version already accepted"


async def test_an_external_agent_is_not_concerned(client: AsyncClient, admin: str) -> None:
    await _require(client, "required")
    external = {"slug": "my-client", "display_name": "C", "kind": "external", "spec": {}}
    created = await client.post(AGENTS, json=external)
    assert created.status_code == 201, created.text


async def test_null_lifts_the_requirement(client: AsyncClient, admin: str) -> None:
    await _require(client, "required")
    await _require(client, None)
    assert ORG_SETTING not in await _stored()
    created = await client.post(AGENTS, json={"slug": "loose", "display_name": "L", "spec": INCOMPLETE})
    assert created.status_code == 201, created.text


async def test_only_its_administrator_requires_it(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    member = {"email": "lead@varga.dev", "role": "project_owner"}
    assert (await client.post(f"{ORG}/varga/members", json=member)).status_code in {200, 201}
    await login(client, "lead@varga.dev")
    refused = await client.patch(f"{ORG}/varga", json={"complete_agents": "required"})
    assert refused.status_code == 403, refused.text
    assert ORG_SETTING not in await _stored()


def _incomplete_in_catalogue(monkeypatch: Any, *slugs: str) -> None:
    """The offline catalogue with these entries leaving their tools and memories to the project, as a
    plugin's agents may (diametral-delivery's, in production)."""
    import dataclasses

    from choregos_api.services import agent_catalogue as service

    base = service.offline_catalogue()

    def loose(entry: Any) -> Any:
        document = dict(entry.document)
        spec = {k: v for k, v in dict(document["spec"]).items() if k not in {"tools", "memories"}}
        return dataclasses.replace(entry, document={**document, "spec": spec})

    view = dataclasses.replace(base, entries=tuple(loose(e) if e.slug in slugs else e for e in base.entries))
    monkeypatch.setattr(service, "offline_catalogue", lambda: view)


def _naming(source: str, actor: str, slug: str) -> str:
    import yaml

    document = yaml.safe_load(source)
    document["actors"][actor]["agent"] = slug
    return yaml.safe_dump(document, sort_keys=False)


async def test_a_workflow_naming_catalogue_agents_already_installed_is_published(
    client: AsyncClient, project: dict[str, Any], monkeypatch: Any
) -> None:
    """Found in production on 2026-11-11 (S62-12): once complete agents were required, adding `a2a:` to a
    template's workflow was refused, because publishing it judged the catalogue agents it names, already
    installed and so never rewritten, as if they were new versions. Only an agent the publication installs
    is judged, and the refusal names it."""
    from choregos_core.dsl import template_yaml

    _incomplete_in_catalogue(monkeypatch, "developer", "tester")
    url = f"/api/v1/projects/{project['id']}/workflow"
    source = _naming(template_yaml("default-simple"), "dev", "developer")
    first = await client.put(url, json={"yaml": source})
    assert first.status_code == 200, first.text
    await _require(client, "required")

    again = await client.put(url, json={"yaml": source.rstrip("\n") + "\n# a new version, the same agents\n"})
    assert again.status_code == 200, again.text

    refused = await client.put(url, json={"yaml": _naming(source, "checker", "tester")})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"].startswith("the agent `tester` of the catalogue entry `tester` leaves ")

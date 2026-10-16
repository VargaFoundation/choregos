"""A GitHub connector is saved only on an owner this organisation installed the App on (ADR 0073)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

PUT = "/api/v1/projects/{id}/connectors/{kind}"


async def _put(
    client: AsyncClient, project: dict[str, Any], kind: str, type_: str, config: dict[str, Any]
) -> Any:
    return await client.put(PUT.format(id=project["id"], kind=kind), json={"type": type_, "config": config})


async def test_a_github_connector_on_an_uninstalled_owner_gets_422_with_the_install_link(
    client: AsyncClient, project: dict[str, Any], github: Any
) -> None:
    github.add_installation(1, "varga")
    refused = await _put(client, project, "scm", "github", {"repo": "stranger/repo"})
    assert refused.status_code == 422, refused.text
    assert "not installed on `stranger`" in refused.text
    assert "https://github.com/apps/choregos-bot/installations/new" in refused.text
    refused_tracker = await _put(client, project, "tracker", "github-issues", {"repo": "stranger/repo"})
    assert refused_tracker.status_code == 422
    accepted = await _put(client, project, "scm", "github", {"repo": "varga/billing"})
    assert accepted.status_code == 200, accepted.text


async def test_an_installation_claimed_by_another_organisation_does_not_open_the_owner(
    client: AsyncClient, project: dict[str, Any], github: Any
) -> None:
    from .github_app_kit import add_organisation, claim_for

    globex = await add_organisation("globex")
    github.add_installation(1, "globex-inc")
    await claim_for(1, "globex-inc", globex)
    refused = await _put(client, project, "scm", "github", {"repo": "globex-inc/private-repo"})
    assert refused.status_code == 422, "typing someone else's repository mints nothing and saves nothing"


async def test_a_legacy_installation_id_is_dropped_on_save(
    client: AsyncClient, project: dict[str, Any], github: Any
) -> None:
    github.add_installation(1, "varga")
    saved = await _put(client, project, "scm", "github", {"repo": "varga/billing", "installation_id": 999})
    assert saved.status_code == 200, saved.text
    assert "installation_id" not in saved.json()["config"]
    listed = (await client.get(f"/api/v1/projects/{project['id']}/connectors")).json()
    assert all("installation_id" not in c["config"] for c in listed)


async def test_without_an_app_nothing_changes(client: AsyncClient, project: dict[str, Any]) -> None:
    """A deployment that has no App (a token, a fake) keeps saving any repository."""
    saved = await _put(client, project, "scm", "github", {"repo": "anyone/anything"})
    assert saved.status_code == 200, saved.text


async def test_a_connector_on_another_host_is_left_alone(
    client: AsyncClient, project: dict[str, Any], github: Any
) -> None:
    saved = await _put(
        client, project, "scm", "github", {"repo": "corp/app", "api_url": "https://ghe.corp.example/api/v3"}
    )
    assert saved.status_code == 200, saved.text


async def test_the_claimed_installation_is_injected_when_the_connector_is_built(
    client: AsyncClient, project: dict[str, Any], github: Any
) -> None:
    from choregos_api.db.models import Connector, Project
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import resolved_configuration
    from sqlalchemy import select

    github.add_installation(41, "varga")
    assert (await _put(client, project, "scm", "github", {"repo": "varga/billing"})).status_code == 200
    async with session_scope(orgs="*") as session:
        row = (await session.execute(select(Connector).where(Connector.kind == "scm"))).scalar_one()
        assert "installation_id" not in row.config, "never stored"
        owner_project = await session.get(Project, project["id"])
        assert owner_project is not None
        config = await resolved_configuration(
            session, owner_project.org_id, "scm", "github", row.config, row.secret_refs, row.secret_ref
        )
    assert config["installation_id"] == 41

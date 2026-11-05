"""A connector bound to a fake's scene is the live eval matrix's alone (S57-11a).

The matrix's seeding declares, in its eval projects, organisation connectors whose configuration holds
`fake_scene: <scene>`, and the API serves them from that scene (`plugin_servers.connector_client`). Two
guards keep any other connector away from a fake: no declaration or edit may set the field, whoever asks
and whatever the deployment allows; and the field is honoured only where the deployment serves eval fakes
(`CHOREGOS_EVAL_FAKE_SCENES`), ignored everywhere else.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.fakes.scenes import EVAL_FAKE_SCENES_ENV, FAKE_SCENE_FIELD
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga/connectors"
BOUND = {FAKE_SCENE_FIELD: "ssh-exposure"}


@pytest.fixture
def eval_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EVAL_FAKE_SCENES_ENV, "1")


async def test_an_organisation_cannot_declare_a_connector_on_a_fake(
    client: AsyncClient, admin: str, eval_fakes: None
) -> None:
    refused = await client.post(ORG, json={"name": "pfsense", "type": "pfsense", "config": BOUND})
    assert refused.status_code == 422, refused.text
    assert FAKE_SCENE_FIELD in refused.text
    assert (await client.get(f"{ORG}/pfsense")).status_code == 404, "nothing was written"


async def test_an_organisation_cannot_edit_a_connector_onto_a_fake(
    client: AsyncClient, admin: str, eval_fakes: None
) -> None:
    declared = await client.post(
        ORG, json={"name": "vendor", "type": "mcp", "config": {"url": "https://vendor.test/mcp"}}
    )
    assert declared.status_code == 201, declared.text
    edited = await client.patch(
        f"{ORG}/vendor", json={"config": {"url": "https://vendor.test/mcp", FAKE_SCENE_FIELD: "default"}}
    )
    assert edited.status_code == 422, edited.text
    assert FAKE_SCENE_FIELD not in (await client.get(f"{ORG}/vendor")).json()["config"]


async def test_a_project_cannot_set_the_field_on_its_own_connector(
    client: AsyncClient, project: dict[str, Any], eval_fakes: None
) -> None:
    refused = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={"type": "internal", "config": {FAKE_SCENE_FIELD: "default"}},
    )
    assert refused.status_code == 422, refused.text
    assert FAKE_SCENE_FIELD in refused.text


async def _client_of_a_bound_row() -> Any:
    """What the broker calls for a connector the matrix wrote itself, on the scene `ssh-exposure`."""
    from choregos_api.db.models import Organization, OrgConnector
    from choregos_api.db.session import session_scope
    from choregos_api.services.plugin_servers import connector_client
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization))).scalar_one()
        row = OrgConnector(
            org_id=org.id,
            name="pfsense",
            kind="firewall",
            type="pfsense",
            config=dict(BOUND),
            availability="selected_projects",
            created_by="eval-matrix",
        )
        session.add(row)
        await session.flush()
        return await connector_client(session, row)


async def test_the_scene_serves_the_matrix_s_connector_where_the_deployment_allows_it(
    client: AsyncClient, admin: str, eval_fakes: None
) -> None:
    connector = await _client_of_a_bound_row()
    assert (await connector.execute("list_aliases", {}))["count"] == 41, "the scene's 41 aliases"
    rules = await connector.execute("list_firewall_rules", {"interface": "wan"})
    assert any(rule["descr"] == "Temporary vendor SSH" for rule in rules["rules"])


async def test_the_field_is_ignored_where_the_deployment_does_not_allow_eval_fakes(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Built as its type says instead: here the suite's process-wide fake (`CHOREGOS_FAKES=1`), never the
    scene; on a real deployment, a pfSense client with no address, which reaches nothing."""
    monkeypatch.delenv(EVAL_FAKE_SCENES_ENV, raising=False)
    connector = await _client_of_a_bound_row()
    assert (await connector.execute("list_aliases", {}))["count"] != 41

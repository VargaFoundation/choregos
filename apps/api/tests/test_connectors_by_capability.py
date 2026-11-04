"""Connectors by capability (ADR 0034, S19-01).

The types come from the registry; a project sees what its workflows demand, and why; a secret is
only written by reference, and the reference is RESOLVED when the connector serves.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

ARRIVAL = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: arrival, version: 1 }
actors:
  coordinator: { type: agent, role: plan, model: "profile:standard" }
  hr: { type: human, group: hr, sla_hours: 24 }
states:
  request: { display: Request, kind: wait }
  plan: { display: Access plan }
  validated: { display: Validated, terminal: true }
transitions:
  - { id: t-plan, from: request, to: plan, by: coordinator, outputs: [plan], gates: [outputs_present] }
  - { id: t-validate, from: plan, to: validated, by: hr }
"""


async def test_the_types_come_from_the_registry(client: AsyncClient, admin: str) -> None:
    types = {(t["kind"], t["type"]): t for t in (await client.get("/api/v1/connectors/types")).json()}
    jira = types[("tracker", "jira")]
    assert jira["available"] is True, "registered, therefore available"
    assert jira["capabilities"] == ["tracker"]
    assert {"api_token", "webhook_secret"} <= set(jira["secret_fields"])
    assert "api_token" not in jira["config_schema"]["properties"]
    assert not [t for (_, t) in types if t == "fake"]


async def test_an_hr_project_shows_neither_repository_nor_ci_nor_train(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    software = [r["capability"] for r in (await client.get(f"/api/v1/projects/{pid}/requirements")).json()]
    assert {"scm", "cd"} <= set(software), "the default template delivers software"
    assert "ci" not in software, "the PR checks are read through the repository (S21-23)"

    assert (
        await client.put(f"/api/v1/projects/{pid}/workflows/arrival", json={"yaml": ARRIVAL})
    ).status_code in {200, 201}
    routing = {"default": "arrival", "rules": []}
    assert (await client.put(f"/api/v1/projects/{pid}/workflow-routing", json=routing)).status_code == 200
    assert (
        await client.post(f"/api/v1/projects/{pid}/workflows/default-simple/deactivate")
    ).status_code == 204

    requirements = (await client.get(f"/api/v1/projects/{pid}/requirements")).json()
    assert [r["capability"] for r in requirements] == ["tracker", "runtime", "gateway"]
    assert any("arrival: the agent coordinator" in reason for reason in requirements[1]["reasons"])
    assert requirements[0]["connector"] is None
    assert requirements[0]["default_type"] is None, "a tracker is not provided by the deployment (S40-05)"
    assert requirements[1]["default_type"], "the runtime is"


async def test_only_what_the_deployment_provides_is_a_platform_default(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """S40-05: `cd` said "Platform default: argocd" and `ci` "tekton" with nothing behind them."""
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    pid = project["id"]
    items = {r["capability"]: r for r in (await client.get(f"/api/v1/projects/{pid}/requirements")).json()}
    assert {"scm", "cd", "runtime"} <= set(items)
    assert items["cd"]["default_type"] is None and items["scm"]["default_type"] is None
    assert items["runtime"]["default_type"] == "k8s_job"


async def test_a_secret_in_clear_gets_422(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    body = {"type": "jira", "config": {"base_url": "https://acme.atlassian.net", "email": "a@b.c",
                                         "project_key": "HR", "api_token": "s3cr3t"}}  # fmt: skip
    refusal = await client.put(f"/api/v1/projects/{pid}/connectors/tracker", json=body)
    assert refusal.status_code == 422 and "in clear" in refusal.text
    assert "s3cr3t" not in refusal.text

    body["config"].pop("api_token")
    for references, reason in (
        ({"api_token": "s3cr3t"}, "reference"),
        ({"api_token": "vault:x"}, "unknown"),
        ({"password": "env:X"}, "secret field"),
    ):
        refusal = await client.put(
            f"/api/v1/projects/{pid}/connectors/tracker", json={**body, "secret_refs": references}
        )
        assert refusal.status_code == 422 and reason in refusal.text, (references, refusal.text)
    unknown = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "trello", "config": {}}
    )
    assert unknown.status_code == 422


async def test_a_reference_is_resolved_when_the_connector_serves(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = project["id"]
    body = {
        "type": "jira",
        "config": {"base_url": "https://acme.atlassian.net", "email": "a@b.c", "project_key": "HR"},
        "secret_refs": {"api_token": "env:CONNECTOR_TEST_JIRA_TOKEN"},
    }
    put = await client.put(f"/api/v1/projects/{pid}/connectors/tracker", json=body)
    assert put.status_code == 200, put.text
    assert put.json()["secret_refs"] == {"api_token": "env:CONNECTOR_TEST_JIRA_TOKEN"}

    monkeypatch.delenv("CONNECTOR_TEST_JIRA_TOKEN", raising=False)
    missing = (await client.post(f"/api/v1/projects/{pid}/connectors/tracker/test")).json()
    assert missing["ok"] is False
    assert "CONNECTOR_TEST_JIRA_TOKEN" in str(missing["checks"])

    monkeypatch.setenv("CONNECTOR_TEST_JIRA_TOKEN", "s3cr3t")
    present = (await client.post(f"/api/v1/projects/{pid}/connectors/tracker/test")).json()
    assert present["ok"] is True, present
    assert "s3cr3t" not in str(present)
    covered = {r["capability"]: r for r in (await client.get(f"/api/v1/projects/{pid}/requirements")).json()}
    assert covered["tracker"]["connector"]["type"] == "jira"
    assert covered["tracker"]["default_type"] is None


async def test_the_tested_adapter_receives_the_resolved_secret_and_the_response_does_not_say_it(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside the fakes: the connector test builds the adapter with the value, never with the
    reference, and nothing of the value comes back in the response."""
    from choregos_adapters import ConnectorTypeSpec, register
    from choregos_adapters.registry import _REGISTRY, _SPECS

    received: list[dict[str, Any]] = []
    spec = ConnectorTypeSpec("probe", ("tracker",), {"type": "object", "properties": {}}, ("api_token",))
    register("tracker", "probe", spec)(lambda cfg: received.append(cfg) or object())
    try:
        pid = project["id"]
        body = {"type": "probe", "config": {}, "secret_refs": {"api_token": "env:CONNECTOR_TEST_PROBE"}}
        assert (await client.put(f"/api/v1/projects/{pid}/connectors/tracker", json=body)).status_code == 200
        monkeypatch.setenv("CONNECTOR_TEST_PROBE", "v4lue")
        monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
        result = await client.post(f"/api/v1/projects/{pid}/connectors/tracker/test")
    finally:
        _REGISTRY.pop(("tracker", "probe"), None)
        _SPECS.pop(("tracker", "probe"), None)
    assert received == [{"api_token": "v4lue"}]
    assert "v4lue" not in result.text


# ── S39-06b: a reference reads connector variables only, and only an administrator writes one ──


def _jira(host: str, reference: str) -> dict[str, Any]:
    return {
        "type": "jira",
        "config": {"base_url": f"https://{host}", "email": "a@b.c", "project_key": "HR"},
        "secret_refs": {"api_token": reference},
    }


async def test_a_project_owner_cannot_write_an_env_reference(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    from .conftest import login

    url = f"/api/v1/projects/{project['id']}/connectors/tracker"
    # The organisation's administrator (the fixture's session) may.
    assert (await client.put(url, json=_jira("acme.atlassian.net", "env:CONNECTOR_X"))).status_code == 200
    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        user = User(email="owner@varga.dev", display_name="owner")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role="project_owner"))
    await login(client, "owner@varga.dev")
    refusaled = await client.put(url, json=_jira("acme.atlassian.net", "env:CONNECTOR_X"))
    assert refusaled.status_code == 422, refusaled.text
    assert "administrator" in refusaled.json()["detail"]


async def test_a_platform_secret_never_leaves_through_a_connector_test(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    import respx
    from choregos_api.db.models import Connector
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    monkeypatch.setenv("CHOREGOS_SESSION_SECRET", "platform-session-secret")
    url = f"/api/v1/projects/{project['id']}/connectors/tracker"
    with respx.mock(assert_all_called=False) as mock:
        host = mock.route(host="evil.test").mock(return_value=httpx.Response(200, json={}))
        # Refused at write, for the administrator too.
        refusaled = await client.put(url, json=_jira("evil.test", "env:CHOREGOS_SESSION_SECRET"))
        assert refusaled.status_code == 422, refusaled.text
        # A reference stored before the allowlist is never resolved: the connector is in error.
        await client.put(url, json=_jira("evil.test", "env:CONNECTOR_X"))
        async with session_scope() as session:
            row = (
                await session.execute(select(Connector).where(Connector.project_id == project["id"]))
            ).scalar_one()
            row.secret_refs = {"api_token": "env:CHOREGOS_SESSION_SECRET"}
        monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
        tested = await client.post(f"{url}/test")
        assert "platform-session-secret" not in tested.text
        assert tested.json()["ok"] is False
        assert not host.called, "nothing was sent to the connector's host"

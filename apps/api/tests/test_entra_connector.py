"""The `entra` connector in the organisation (S20-03): its operations are born declared, with their
schema; a read is allowed, a write goes through a validation; a run reaches what the policy opens,
through the broker, and an account outside the administrative unit is refused, named.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.entra import FakeEntra

    fake_entra = FakeEntra()
    monkeypatch.setattr(choregos_adapters, "FAKE_ENTRA", fake_entra)
    monkeypatch.setenv("CONNECTOR_ENTRA_CLIENT_SECRET", "connector-secret")
    yield fake_entra


async def _declare(client: AsyncClient) -> dict[str, Any]:
    body = {"name": "entra-acme", "type": "entra", "config": {"tenant_id": "acme", "client_id": "choregos"},
             "secret_refs": {"client_secret": "env:CONNECTOR_ENTRA_CLIENT_SECRET"}}  # fmt: skip
    created = await client.post(f"{ORG}/connectors", json=body)
    assert created.status_code == 201, created.text
    return {o["name"]: o for o in created.json()["operations"]}


async def _run(
    client: AsyncClient, project: dict[str, Any], patterns: list[str]
) -> tuple[str, dict[str, str]]:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    spec = {"mcp_servers": [{"connector": "entra-acme", "tools": patterns}]}
    created = await client.post(
        f"{ORG}/agents", json={"slug": "coordinator", "display_name": "Coordinator", "spec": spec}
    )
    assert created.status_code == 201, created.text
    async with session_scope() as session:
        item = WorkItem(
            project_id=project["id"], tracker_key="varga/hr#1", title="Arrival of Lea", state="plan"
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-entra-1",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="plan",
                transition_id="t-plan",
                status="running",
                agent_slug="coordinator",
                agent_version=1,
            )
        )
    token = mint_run_token(
        "run-entra-1", project_slug="billing-api", work_item_key="varga/hr#1", ttl_minutes=30
    )
    return "/api/v1/internal/runs/run-entra-1/tools", {"Authorization": f"Bearer {token}"}


async def test_a_directorys_operations_are_born_declared_read_allowed_write_validated(
    client: AsyncClient, admin: str, graph: Any
) -> None:
    operations = await _declare(client)
    assert {n: o["policy"] for n, o in operations.items()} == {
        "add_to_group": "approval",
        "create_user": "approval",
        "disable_user": "approval",
        "get_user": "allowed",
        "remove_from_group": "approval",
        "revoke_sessions": "approval",
    }
    assert operations["create_user"]["input_schema"]["required"] == ["upn", "display_name"]


async def test_a_run_reads_the_directory_and_only_writes_by_proposing_an_action(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    graph.add_account("lea@acme.test")
    await _declare(client)
    base, headers = await _run(client, project, ["*"])
    tools = {o["name"]: o for o in (await client.get(base, headers=headers)).json()["tools"]}
    assert "needs a human approval" not in tools["entra-acme__get_user"]["description"]
    assert "needs a human approval" in tools["entra-acme__create_user"]["description"]

    read_back = await client.post(
        f"{base}/entra-acme__get_user", headers=headers, json={"upn": "lea@acme.test"}
    )
    assert read_back.status_code == 200 and read_back.json()["status_code"] == 200, read_back.text
    assert read_back.json()["result"]["structuredContent"]["user"]["userPrincipalName"] == "lea@acme.test"
    assert (await client.post(f"{base}/entra-acme__get_user", headers=headers, json={})).status_code == 400
    # A write under validation: the call proposes an action, the directory does not move.
    proposed = await client.post(
        f"{base}/entra-acme__create_user",
        headers=headers,
        json={"upn": "paul@acme.test", "display_name": "Paul"},
    )
    assert proposed.json()["status_code"] == 202, proposed.text
    assert [c["userPrincipalName"] for c in graph.accounts.values()] == ["lea@acme.test"]


async def test_even_when_open_a_gesture_is_born_as_an_action_and_reaches_nothing_directly(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    """An allowed write, called by a run, does not reach the directory from the request: it is born
    as a governed action, approved by the policy, that the `ActionWorkflow` will play (#241), and
    where the refusal of an account outside the unit will be said (proved end to end, in Temporal)."""
    graph.add_account("pdg@acme.test", in_unit=False)
    await _declare(client)
    opened = await client.patch(
        f"{ORG}/connectors/entra-acme/operations/disable_user", json={"policy": "allowed"}
    )
    assert opened.status_code == 200
    base, headers = await _run(client, project, ["disable_*"])
    call = (
        await client.post(f"{base}/entra-acme__disable_user", headers=headers, json={"upn": "pdg@acme.test"})
    ).json()
    assert call["status_code"] == 202, call
    path = f"/api/v1/projects/{project['id']}/actions/{call['result']['action']}"
    action = (await client.get(path)).json()
    assert (action["origin"], action["status"]) == ("tool", "approved")
    assert action["decisions"][0]["by"] == "policy"
    assert all(c["accountEnabled"] for c in graph.accounts.values()), "nothing moved in the directory"

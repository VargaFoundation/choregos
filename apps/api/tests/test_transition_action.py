"""A system transition proposes a governed action, on a date (S20-05) - the API side.

- A work item's fields change by `PATCH /work-items/{id}`, validated by ITS workflow, and the
  interpreter learns it (`fields_changed`): that is what re-arms an action date.
- An unknown effect is refused when the workflow is published, not when a work item arrives.
- The PROJECT's policy counts: an operation it forbids itself is not proposed, and an approved
  action does not run it.
- When every operation is allowed, the policy decides and the action is born approved; otherwise a
  person decides, and a rejection warns the work item that waits for it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga"
ARRIVAL = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: onboarding
  version: 1
  inputs:
    type: object
    required: [upn]
    properties:
      upn: {type: string}
      start_date: {type: string, format: date-time}
      position: {type: string}
initial: prepared
actors:
  platform: {type: system}
states:
  prepared: {display: Prepared, kind: wait}
  to_review: {display: To review, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - id: t-accounts
    from: prepared
    to: done
    by: platform
    action:
      kind: arrival.accounts
      title: "The accounts of {{ fields.upn }}"
      params: {upn: "{{ fields.upn }}"}
      effects:
        - effect: EFFECT
          with:
            connector: entra-acme
            operation: create_user
            arguments: {upn: "{{ params.upn }}", display_name: Lea}
      not_before: fields.start_date - 10d
    on_fail: {to: prepared, max_attempts: 1, escalate_to: to_review}
"""


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.entra import FakeEntra

    fake_entra = FakeEntra()
    monkeypatch.setattr(choregos_adapters, "FAKE_ENTRA", fake_entra)
    monkeypatch.setenv("CONNECTOR_ENTRA_CLIENT_SECRET", "connector-secret")
    yield fake_entra


async def _directory(client: AsyncClient) -> None:
    body = {"name": "entra-acme", "type": "entra", "config": {"tenant_id": "acme", "client_id": "choregos"},
            "secret_refs": {"client_secret": "env:CONNECTOR_ENTRA_CLIENT_SECRET"}}  # fmt: skip
    created = await client.post(f"{ORG}/connectors", json=body)
    assert created.status_code == 201, created.text


async def _publish(client: AsyncClient, pid: str, effect_name: str = "connector.call") -> Any:
    return await client.put(
        f"/api/v1/projects/{pid}/workflows/onboarding", json={"yaml": ARRIVAL.replace("EFFECT", effect_name)}
    )


async def _work_item(client: AsyncClient, pid: str, fields: dict[str, Any]) -> dict[str, Any]:
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Arrival of Lea", "workflow": "onboarding", "fields": fields},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def test_a_changed_field_is_validated_by_its_workflow_and_the_interpreter_learns_it(
    client: AsyncClient, project: dict[str, Any], graph: Any, app: Any
) -> None:
    from choregos_api.temporal import FakeTemporal, get_temporal

    pid = project["id"]
    assert (await _publish(client, pid)).status_code == 200
    work_item = await _work_item(client, pid, {"upn": "lea@acme.test", "start_date": "2026-11-12T09:00:00Z"})
    base = f"/api/v1/work-items/{work_item['id']}"

    moved = await client.patch(
        base, json={"fields": {"start_date": "2026-11-30T09:00:00Z", "position": "p14"}}
    )
    assert moved.status_code == 200, moved.text
    expected = {"upn": "lea@acme.test", "start_date": "2026-11-30T09:00:00Z", "position": "p14"}
    assert moved.json()["fields"] == expected
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    assert (work_item["temporal_wf_id"], "fields_changed", {"fields": expected}) in fake.signals

    removed = await client.patch(base, json={"fields": {"position": None}})
    assert removed.status_code == 200 and "position" not in removed.json()["fields"], "`null` removes a field"
    refused = await client.patch(base, json={"fields": {"start_date": 20261130}})
    assert refused.status_code == 422 and refused.json()["errors"][0]["loc"] == ["fields", "start_date"]
    required = await client.patch(base, json={"fields": {"upn": None}})
    assert required.status_code == 422, "a required field cannot be removed"

    await client.post(f"{ORG}/members", json={"email": "reader@varga.dev", "role": "viewer"})
    reader = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(reader, "reader@varga.dev")
        assert (await reader.patch(base, json={"fields": {"position": "p16"}})).status_code == 403
    finally:
        await reader.aclose()


async def test_an_unknown_effect_is_refused_at_publication(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    refused = await _publish(client, project["id"], effect_name="erase.everything")
    assert refused.status_code == 422, refused.text
    assert refused.json()["errors"][0]["code"] == "action.effect_unknown"
    assert "erase.everything" in refused.text


async def test_an_operation_the_project_forbids_is_neither_proposed_nor_run(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope
    from choregos_api.effects import EffectContext, EffectRefused, effect

    pid = project["id"]
    await _directory(client)
    tightened = await client.put(
        f"/api/v1/projects/{pid}/operations/entra-acme/create_user", json={"policy": "forbidden"}
    )
    assert tightened.status_code == 200, tightened.text
    call = {"connector": "entra-acme", "operation": "create_user",
            "arguments": {"upn": "lea@acme.test", "display_name": "Lea"}}  # fmt: skip
    proposed = await client.post(
        f"/api/v1/projects/{pid}/actions",
        json={"kind": "x", "title": "x", "effects": [{"effect": "connector.call", "with": call}]},
    )
    assert proposed.status_code == 422 and "forbidden in this project" in proposed.text
    async with session_scope() as session:
        project_row = await session.get(Project, pid)
        assert project_row is not None
        action = Action(
            org_id=project_row.org_id,
            project_id=pid,
            origin="transition",
            kind="x",
            title="x",
            params={},
            effects=[],
            proposed_by={},
            approval={},
            decisions=[],
            status="approved",
        )
        session.add(action)
        await session.flush()
        with pytest.raises(EffectRefused, match="is forbidden to this project"):
            await effect("connector.call")(EffectContext(session, action, project_row), call)
    assert graph.accounts == {}, "approved or not, an operation the project forbids itself never leaves"


async def test_an_operation_reserved_to_other_groups_is_forbidden_to_this_project(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    pid = project["id"]
    await _directory(client)
    reserved = await client.patch(
        f"{ORG}/connectors/entra-acme/operations/create_user", json={"groups": ["rh"]}
    )
    assert reserved.status_code == 200, reserved.text
    call = {"connector": "entra-acme", "operation": "create_user",
            "arguments": {"upn": "lea@acme.test", "display_name": "Lea"}}  # fmt: skip
    proposed = await client.post(
        f"/api/v1/projects/{pid}/actions",
        json={"kind": "x", "title": "x", "effects": [{"effect": "connector.call", "with": call}]},
    )
    assert proposed.status_code == 422 and "entra-acme/create_user: forbidden" in proposed.text


async def _propose_from_the_transition(pid: str, item_id: str, attempt: int = 1, **action: Any) -> Any:
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.actions import propose_for_transition
    from choregos_core.dsl import parse_workflow

    spec = parse_workflow(ARRIVAL.replace("EFFECT", "connector.call"))[0].transitions[0].action
    assert spec is not None
    document = {**spec.model_dump(mode="json", by_alias=True, exclude_none=True), **action}
    async with session_scope() as session:
        project_row, item = await session.get(Project, pid), await session.get(WorkItem, item_id)
        assert project_row is not None and item is not None
        proposal = await propose_for_transition(
            session, project_row, item, document, transition="t-accounts", attempt=attempt, workflow_id="wi-x"
        )
        return proposal.id, proposal.status, proposal.decisions, proposal.title


async def test_the_policy_decides_when_everything_is_allowed_otherwise_a_person(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    pid = project["id"]
    await _directory(client)
    assert (await _publish(client, pid)).status_code == 200
    work_item = await _work_item(client, pid, {"upn": "lea@acme.test"})

    identifier, status, decisions, title = await _propose_from_the_transition(pid, work_item["id"])
    assert (status, decisions, title) == ("pending_approval", [], "The accounts of lea@acme.test"), (
        "a write is `approval` by default: a person decides"
    )
    assert (await _propose_from_the_transition(pid, work_item["id"]))[0] == identifier, (
        "the same attempt finds the same action: a replayed activity does not create another"
    )
    opened = await client.patch(
        f"{ORG}/connectors/entra-acme/operations/create_user", json={"policy": "allowed"}
    )
    assert opened.status_code == 200
    other, status, decisions, _ = await _propose_from_the_transition(pid, work_item["id"], attempt=2)
    assert other != identifier and status == "approved" and decisions[0]["by"] == "policy"
    demanded = {"approval": {"approvers": [{"role": "project_owner", "min": 1}]}}
    _, status, _, _ = await _propose_from_the_transition(pid, work_item["id"], attempt=3, **demanded)
    assert status == "pending_approval", "the workflow can add a validation, never remove one"
    # What undoes it is a write too, run without another decision: it counts.
    compensated = [
        {
            "effect": "connector.call",
            "with": {"connector": "entra-acme", "operation": "create_user", "arguments": {"upn": "x"}},
            "compensate": {
                "effect": "connector.call",
                "with": {"connector": "entra-acme", "operation": "disable_user", "arguments": {"upn": "x"}},
            },
        }
    ]
    _, status, _, _ = await _propose_from_the_transition(pid, work_item["id"], attempt=4, effects=compensated)
    assert status == "pending_approval", "disable_user is `approval`: the compensation imposes it"


def test_a_template_that_escapes_the_sandbox_is_a_refusal_not_a_failure() -> None:
    from choregos_api.effects import EffectRefused, render

    for template in ("{{ fields.__class__.__mro__ }}", "{{ fields.upn"):
        with pytest.raises(EffectRefused, match="template refused"):
            render(template, {"fields": {"upn": "lea@acme.test"}})


async def test_a_rejection_warns_the_work_item_that_waits(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    from choregos_api.temporal import get_temporal

    pid = project["id"]
    await _directory(client)
    assert (await _publish(client, pid)).status_code == 200
    work_item = await _work_item(client, pid, {"upn": "lea@acme.test"})
    identifier, *_ = await _propose_from_the_transition(pid, work_item["id"])
    rejection = await client.post(
        f"/api/v1/projects/{pid}/actions/{identifier}/decision",
        json={"decision": "reject", "reason": "Lea is not coming any more"},
    )
    assert rejection.status_code == 200 and rejection.json()["status"] == "rejected", rejection.text
    # The interpreter the proposal recorded: it is the one that waits (S20-06).
    awaited = ("wi-x", "action_settled", {"action_id": identifier, "status": "rejected"})
    assert awaited in get_temporal().signals  # type: ignore[attr-defined]

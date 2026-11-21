"""Who decides a workflow's action comes from its work item (ADR 0196): an approver group written
`{{ fields.approver_group }}` names the group that field holds, rendered when the action is proposed,
and `excluded` names the people who may not decide it — the person an access grants, the person who
asked — whatever their rank, an organisation administrator included.

- A member of the rendered group with the rank approves; a project owner outside it does not.
- The person the action concerns and the person who asked are refused, even as administrators.
- A group that renders empty, or a field the work item does not hold, refuses the proposal: a rule
  nobody can satisfy, or that anybody could, is not presented.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/northwind"
GRANT = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: grant
  version: 1
  inputs:
    type: object
    required: [upn, group_id]
    properties:
      upn: {type: string}
      group_id: {type: string}
      approver_group: {type: string}
      requester: {type: string}
initial: requested
actors:
  platform: {type: system}
states:
  requested: {display: Requested, kind: wait}
  to_review: {display: To review, kind: wait}
  granted: {display: Granted, terminal: true}
transitions:
  - id: t-grant
    from: requested
    to: granted
    by: platform
    action:
      kind: access.grant
      title: "{{ fields.upn }} into {{ fields.group_id }}"
      params: {upn: "{{ fields.upn }}", group_id: "{{ fields.group_id }}"}
      effects:
        - effect: connector.call
          with:
            connector: entra-acme
            operation: add_to_group
            arguments: {upn: "{{ params.upn }}", group_id: "{{ params.group_id }}"}
      approval:
        approvers: [{role: approver, group: "owners-{{ fields.approver_group }}"}]
        excluded: ["{{ fields.upn }}", "{{ fields.requester }}"]
        step_up_minutes: 5
    on_fail: {to: requested, max_attempts: 1, escalate_to: to_review}
"""


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import tranibor_adapters
    from tranibor_adapters.fakes.entra import FakeEntra

    fake_entra = FakeEntra()
    monkeypatch.setattr(tranibor_adapters, "FAKE_ENTRA", fake_entra)
    monkeypatch.setenv("CONNECTOR_ENTRA_CLIENT_SECRET", "connector-secret")
    yield fake_entra


async def _member(client: AsyncClient, email: str, role: str, groups: list[str]) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import User
    from tranibor_api.db.session import session_scope

    added = await client.post(f"{ORG}/members", json={"email": email, "role": role})
    assert added.status_code in {200, 201}, added.text
    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.groups = groups


async def _decide(app: Any, email: str, path: str) -> Any:
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(other, email)
        return await other.post(f"{path}/decision", json={"decision": "approve"})
    finally:
        await other.aclose()


async def _setup(client: AsyncClient, pid: str) -> None:
    body = {"name": "entra-acme", "type": "entra", "config": {"tenant_id": "acme", "client_id": "choregos"},
            "secret_refs": {"client_secret": "env:CONNECTOR_ENTRA_CLIENT_SECRET"}}  # fmt: skip
    assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    published = await client.put(f"/api/v1/projects/{pid}/workflows/grant", json={"yaml": GRANT})
    assert published.status_code == 200, published.text


async def _propose(client: AsyncClient, pid: str, fields: dict[str, Any], attempt: int = 1) -> Any:
    from tranibor_api.db.models import Project, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_api.services.actions import propose_for_transition
    from tranibor_core.dsl import parse_workflow

    created = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Access", "workflow": "grant", "fields": fields, "start": False},
    )
    assert created.status_code == 201, created.text
    spec = parse_workflow(GRANT)[0].transitions[0].action
    assert spec is not None
    document = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
    async with session_scope() as session:
        project_row = await session.get(Project, pid)
        item = await session.get(WorkItem, created.json()["id"])
        assert project_row is not None and item is not None
        return await propose_for_transition(
            session, project_row, item, document, transition="t-grant", attempt=attempt, workflow_id="wi-g"
        )


async def test_the_group_the_work_item_names_decides_and_the_people_it_concerns_do_not(
    client: AsyncClient, project: dict[str, Any], graph: Any, app: Any, admin: str
) -> None:
    pid = project["id"]
    await _setup(client, pid)
    proposed = await _propose(
        client,
        pid,
        {"upn": "lea@northwind.dev", "group_id": "g-crm", "approver_group": "crm", "requester": admin},
    )
    assert proposed.status == "pending_approval"
    assert proposed.approval["approvers"] == [{"role": "approver", "group": "owners-crm", "min": 1}]
    assert proposed.approval["excluded"] == ["lea@northwind.dev", admin], "rendered when proposed"
    path = f"/api/v1/projects/{pid}/actions/{proposed.id}"

    await _member(client, "lea@northwind.dev", "project_owner", ["owners-crm"])
    await _member(client, "owner@northwind.dev", "project_owner", ["platform-team"])
    await _member(client, "crm@northwind.dev", "approver", ["owners-crm"])

    concerned = await _decide(app, "lea@northwind.dev", path)
    assert concerned.status_code == 403 and "may not decide" in concerned.text, "the person it grants"
    asked = await client.post(f"{path}/decision", json={"decision": "approve"})
    assert asked.status_code == 403 and "may not decide" in asked.text, "who asked, administrator or not"
    outside = await _decide(app, "owner@northwind.dev", path)
    assert outside.status_code == 403 and "owners-crm" in outside.text, "a rank without the group"
    assert (await client.get(path)).json()["status"] == "pending_approval"

    owner = await _decide(app, "crm@northwind.dev", path)
    assert owner.status_code == 200 and owner.json()["status"] == "approved", owner.text


async def test_an_exclusion_that_renders_empty_excludes_nobody(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    pid = project["id"]
    await _setup(client, pid)
    proposed = await _propose(
        client, pid, {"upn": "lea@northwind.dev", "group_id": "g", "approver_group": "crm", "requester": " "}
    )
    assert proposed.approval["excluded"] == ["lea@northwind.dev"]


@pytest.mark.parametrize(
    "fields",
    [
        {"upn": "lea@northwind.dev", "group_id": "g", "approver_group": "", "requester": "x@northwind.dev"},
        {"upn": "lea@northwind.dev", "group_id": "g", "requester": "x@northwind.dev"},
        {"upn": "lea@northwind.dev", "group_id": "g", "approver_group": "crm"},
    ],
    ids=["group-empty", "group-field-absent", "excluded-field-absent"],
)
async def test_a_rule_that_cannot_be_rendered_refuses_the_proposal(
    client: AsyncClient, project: dict[str, Any], graph: Any, fields: dict[str, Any]
) -> None:
    from tranibor_api.effects import EffectRefused

    pid = project["id"]
    await _setup(client, pid)
    with pytest.raises(EffectRefused):
        await _propose(client, pid, fields)


def test_the_validator_refuses_an_approval_that_reads_anything_but_a_declared_field() -> None:
    from tranibor_core.dsl import parse_workflow

    refused = {
        "owners-{{ fields.approver_group }}": None,
        "{{ fields.unknown }}": "action.approval_field_unknown",
        "{{ fields.upn | upper }}": "action.approval_template",
        "{{ fields.upn }}{{ fields.requester }}": "action.approval_template",
        "{team}-{{ fields.upn }}": "action.approval_template",
    }
    for group, code in refused.items():
        _, report = parse_workflow(GRANT.replace("owners-{{ fields.approver_group }}", group), strict=False)
        codes = [e["code"] for e in report.as_dict()["errors"]]
        assert codes == ([code] if code else []), (group, codes)
    _, report = parse_workflow(
        GRANT.replace('"{{ fields.requester }}"', '"by-{{ fields.requester }}"'), strict=False
    )
    assert [e["code"] for e in report.as_dict()["errors"]] == ["action.approval_template"]

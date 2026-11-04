"""A human task (S20-06): a form and an attestation, API side.

`complete` accepts only what the form describes, required fields included, and the attestation when
the task asks for one; the values become fields of the work item (a following action reads them),
and the decision keeps the values and the attested sentence, as it was shown: the proof.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

BADGE = """
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
      badge_uid: {type: string}
initial: badge
actors:
  reception: {type: human, group: reception}
states:
  badge: {display: Badge to hand over, kind: wait}
  fait: {display: Done, terminal: true}
transitions:
  - id: t-badge
    from: badge
    to: fait
    by: reception
    task:
      title: Hand over the badge
      form:
        type: object
        required: [badge_uid]
        properties:
          badge_uid: {type: string, minLength: 8}
      attest: I handed the badge over in person to its holder
"""
ATTESTED = "I handed the badge over in person to its holder"


async def _waiting_work_item(
    client: AsyncClient, pid: str, kind: str = "task", yaml: str = BADGE
) -> dict[str, Any]:
    """The work item, and the request the interpreter would have created on reaching the task."""
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    published = await client.put(f"/api/v1/projects/{pid}/workflows/onboarding", json={"yaml": yaml})
    assert published.status_code == 200, published.text
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Lea arrives", "workflow": "onboarding", "fields": {"upn": "lea@acme.test"}},
    )
    assert created.status_code == 201, created.text
    ticket = dict(created.json())
    payload = (
        {"summary": "Hand over the badge", "form": {"type": "object", "required": ["badge_uid"],
         "properties": {"badge_uid": {"type": "string", "minLength": 8}}}, "attest": ATTESTED}
        if kind == "task"
        else {"summary": "Validation required"}
    )  # fmt: skip
    async with session_scope() as session:
        session.add(
            HumanRequest(
                work_item_id=ticket["id"],
                project_id=pid,
                transition_id="t-badge",
                kind=kind,
                payload=payload,
                requested_at=utcnow(),
            )
        )
    return ticket


async def test_a_task_is_filled_attested_and_its_values_become_fields(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import FakeTemporal, get_temporal

    ticket = await _waiting_work_item(client, project["id"])
    base = f"/api/v1/work-items/{ticket['id']}"
    decisions = f"{base}/decisions"

    approved = await client.post(decisions, json={"kind": "approve"})
    assert approved.status_code == 422 and "is completed" in approved.text
    empty = await client.post(decisions, json={"kind": "complete", "values": {}, "attested": True})
    assert empty.status_code == 422 and "badge_uid" in empty.text, "a required field is missing"
    short = await client.post(
        decisions, json={"kind": "complete", "values": {"badge_uid": "04A1"}, "attested": True}
    )
    assert short.status_code == 422 and short.json()["errors"][0]["loc"] == ["values", "badge_uid"]
    outside = await client.post(
        decisions,
        json={"kind": "complete", "values": {"badge_uid": "04A1B2C3", "upn": "x"}, "attested": True},
    )
    assert outside.status_code == 422 and "not in the task's form" in outside.text, (
        "a task fills only its form"
    )
    without = await client.post(decisions, json={"kind": "complete", "values": {"badge_uid": "04A1B2C3"}})
    assert without.status_code == 422 and ATTESTED in without.text, (
        "the attestation is asked for, word for word"
    )

    done = await client.post(
        decisions, json={"kind": "complete", "values": {"badge_uid": "04A1B2C3"}, "attested": True}
    )
    assert done.status_code == 202, done.text
    decision = done.json()["decision"]
    assert (decision["kind"], decision["approved"]) == ("task", True)
    assert decision["values"] == {"badge_uid": "04A1B2C3"} and decision["attestation"] == ATTESTED
    assert (await client.get(base)).json()["fields"] == {"upn": "lea@acme.test", "badge_uid": "04A1B2C3"}
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    (sent,) = [p for _, name, p in fake.signals if name == "human_decision"]
    assert sent["values"] == {"badge_uid": "04A1B2C3"}, "the interpreter receives the values"
    timeline = (await client.get(f"{base}/timeline")).text
    assert ATTESTED in timeline, "the proof reads in the history of the work item"


async def test_an_impossible_task_is_sent_back_and_complete_only_holds_for_a_task(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    ticket = await _waiting_work_item(client, project["id"], kind="approval")
    decisions = f"/api/v1/work-items/{ticket['id']}/decisions"
    refusal = await client.post(decisions, json={"kind": "complete", "values": {"badge_uid": "04A1B2C3"}})
    assert refusal.status_code == 422 and "only a task is completed" in refusal.text

    other = await _another_waiting_work_item(client, project["id"])
    sent_back = await client.post(
        f"/api/v1/work-items/{other}/decisions", json={"kind": "reject", "reason": "reader out of order"}
    )
    assert sent_back.status_code == 202 and sent_back.json()["decision"]["approved"] is False


async def _another_waiting_work_item(client: AsyncClient, pid: str) -> str:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    created = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Paul arrives", "workflow": "onboarding", "fields": {"upn": "paul@acme.test"}},
    )
    assert created.status_code == 201, created.text
    async with session_scope() as session:
        session.add(
            HumanRequest(
                work_item_id=created.json()["id"],
                project_id=pid,
                transition_id="t-badge",
                kind="task",
                payload={"form": {"type": "object", "properties": {"badge_uid": {"type": "string"}}}},
                requested_at=utcnow(),
            )
        )
    return str(created.json()["id"])


async def test_the_values_of_a_task_remain_fields_validated_by_the_workflow(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The form does not say everything: `metadata.inputs` has the last word on a field."""
    hex_only = BADGE.replace(
        "      badge_uid: {type: string}\n", "      badge_uid: {type: string, pattern: '^[0-9A-F]+$'}\n", 1
    )
    assert hex_only != BADGE
    ticket = await _waiting_work_item(client, project["id"], yaml=hex_only)
    refusal = await client.post(
        f"/api/v1/work-items/{ticket['id']}/decisions",
        json={"kind": "complete", "values": {"badge_uid": "zz-not-hex"}, "attested": True},
    )
    assert refusal.status_code == 422 and refusal.json()["errors"][0]["loc"] == ["fields", "badge_uid"]

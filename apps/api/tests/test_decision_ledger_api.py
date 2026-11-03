"""The decision ledger seen from the API (ADR 0121 §6, §7, S52-05): calibration measured per question and
model version, and an ended work item whose issue is reopened marks its decisions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import choregos_contracts as contracts
import pytest
from httpx import AsyncClient

REPO = {"full_name": "varga/billing-api"}
KEY = "varga/billing-api#7"


async def _work_item(project: dict[str, Any], key: str = KEY, *, closed: bool = False) -> tuple[str, str]:
    """A work item and one run of it: `(work item id, run id)`."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = WorkItem(
            project_id=project["id"],
            tracker_key=key,
            title="Credit notes",
            state="deployed_prod" if closed else "inbox",
            closed_at=datetime.now(UTC) if closed else None,
        )
        session.add(item)
        await session.flush()
        run = Run(
            id=f"{item.id}-t-route-1",
            work_item_id=item.id,
            project_id=project["id"],
            transition_id="t-route",
            stage_role="route_ticket",
        )
        session.add(run)
        return item.id, run.id


async def _record(
    project: dict[str, Any],
    workflow: str,
    decisions: list[tuple[str, float, bool | None]],
    *,
    model_version: str | None = "laya-0.4.21",
    source: str = "decision_model",
    closed: bool = False,
) -> list[str]:
    """One work item per decision, `(question, probability, right)`: right → ended in success, wrong →
    sent back by a person, `None` → nothing observed yet. Returns the work items' ids."""
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.services import (
        DecidedBy,
        record_decisions,
        record_human_verdict,
        record_terminal_outcome,
    )
    from choregos_core.decision_ledger import Answered

    async with session_scope() as session:
        org_id = (await session.get(Project, project["id"])).org_id  # type: ignore[union-attr]
    items: list[str] = []
    for index, (question, probability, right) in enumerate(decisions):
        item_id, run_id = await _work_item(project, f"{KEY}-{source}-{model_version}-{index}", closed=closed)
        items.append(item_id)
        decided = DecidedBy(
            org_id=org_id,
            project_id=project["id"],
            project_slug=project["slug"],
            work_item_id=item_id,
            work_item_key=f"K-{index}",
            run_id=run_id,
            workflow=workflow,
            workflow_version=1,
            step="inbox",
            transition_id="t-route",
            source=source,
            model="platform/decide",
            model_version=model_version,
            decided_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        async with session_scope() as session:
            await record_decisions(session, decided, [Answered(question, "billing", probability)])
            if right is True:
                await record_terminal_outcome(session, item_id, "success")
            elif right is False:
                await record_human_verdict(session, item_id, datetime.now(UTC), approved=False)
    return items


async def _workflow_name(client: AsyncClient, project: dict[str, Any]) -> str:
    return str((await client.get(f"/api/v1/projects/{project['id']}/workflows")).json()[0]["name"])


async def test_the_contract_declares_the_calibration_route() -> None:
    spec = contracts.load_openapi()
    route = spec["paths"]["/projects/{id}/workflows/{name}/steps/{step}/calibration"]["get"]
    assert route["operationId"] == "getStepCalibration"
    schemas = spec["components"]["schemas"]
    assert set(schemas["StepCalibration"]["required"]) == {
        "workflow",
        "step",
        "target_accuracy",
        "min_observed",
    }
    assert schemas["QuestionCalibration"]["properties"]["source"]["enum"] == ["decision_model", "agent"]
    from choregos_api.schemas import QuestionCalibration, StepCalibration

    assert set(StepCalibration.model_fields) == set(schemas["StepCalibration"]["properties"])
    assert set(QuestionCalibration.model_fields) == set(schemas["QuestionCalibration"]["properties"])


async def test_a_step_with_no_decision_measures_nothing(client: AsyncClient, project: dict[str, Any]) -> None:
    name = await _workflow_name(client, project)
    answer = await client.get(f"/api/v1/projects/{project['id']}/workflows/{name}/steps/inbox/calibration")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "workflow": name,
        "step": "inbox",
        "target_accuracy": 0.9,
        "min_observed": 30,
        "questions": [],
    }


async def test_calibration_per_question_and_model_version(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    name = await _workflow_name(client, project)
    # route, Laya: 20 at 0.95 (19 right), 20 at 0.75 (10 right), 2 not observed yet.
    decisions: list[tuple[str, float, bool | None]] = [("route", 0.95, i < 19) for i in range(20)]
    decisions += [("route", 0.75, i < 10) for i in range(20)]
    decisions += [("route", 0.9, None), ("route", 0.6, None)]
    await _record(project, name, decisions)
    await _record(project, name, [("route", 0.8, True)] * 3, model_version="laya-0.5.0")
    await _record(project, name, [("done", 0.7, True)], source="agent", model_version=None)

    answer = await client.get(
        f"/api/v1/projects/{project['id']}/workflows/{name}/steps/inbox/calibration", params={"target": 0.9}
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    groups = {(q["question"], q["source"], q["model_version"]): q for q in body["questions"]}
    assert set(groups) == {
        ("done", "agent", None),
        ("route", "decision_model", "laya-0.4.21"),
        ("route", "decision_model", "laya-0.5.0"),
    }, "one calibration per question, source and model version: a new version starts again"
    laya = groups[("route", "decision_model", "laya-0.4.21")]
    assert (laya["count"], laya["observed"]) == (42, 40)
    assert laya["accuracy"] == pytest.approx(29 / 40, abs=1e-4)
    # Bin 9: 0.95 said, 0.95 right; bin 7: 0.75 said, 0.5 right → (20 × 0 + 20 × 0.25) / 40.
    assert laya["ece"] == pytest.approx(0.125, abs=1e-4)
    bins = {b["lower"]: b for b in laya["bins"]}
    assert bins[0.9]["count"] == 20 and bins[0.9]["accuracy"] == 0.95
    assert bins[0.7]["count"] == 20 and bins[0.7]["mean_probability"] == 0.75 and bins[0.7]["accuracy"] == 0.5
    points = {p["threshold"]: p for p in laya["thresholds"]}
    assert points[0.5]["coverage"] == 1.0 and points[0.8]["coverage"] == 0.5
    assert points[0.8]["accuracy"] == 0.95
    assert laya["suggested_threshold"] == 0.8 and laya["suggestion_reason"] is None
    newer = groups[("route", "decision_model", "laya-0.5.0")]
    assert newer["suggested_threshold"] is None
    assert newer["suggestion_reason"] == "3 observed decision(s): a threshold is suggested from 30 on"

    stricter = await client.get(
        f"/api/v1/projects/{project['id']}/workflows/{name}/steps/inbox/calibration", params={"target": 0.99}
    )
    (laya_strict,) = [q for q in stricter.json()["questions"] if q["model_version"] == "laya-0.4.21"]
    assert laya_strict["suggested_threshold"] is None
    assert "reaches 99% accuracy" in laya_strict["suggestion_reason"]


async def test_the_calibration_route_refuses_what_it_cannot_measure(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    name = await _workflow_name(client, project)
    base = f"/api/v1/projects/{project['id']}/workflows"
    assert (await client.get(f"{base}/nope/steps/inbox/calibration")).status_code == 404
    for target in ("0", "1.5", "high"):
        refused = await client.get(f"{base}/{name}/steps/inbox/calibration", params={"target": target})
        assert refused.status_code == 422, (target, refused.text)


# ───────────────────────── reopened ─────────────────────────


async def _post(client: AsyncClient, delivery: str, body: dict[str, Any]) -> None:
    answer = await client.post(
        "/api/v1/webhooks/github",
        content=json.dumps(body).encode(),
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": delivery,
            "content-type": "application/json",
        },
    )
    assert answer.status_code == 202, answer.text


async def _reopened(item_id: str) -> set[bool]:
    from choregos_api.db.models import DecisionRecordRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(
            select(DecisionRecordRow.reopened).where(DecisionRecordRow.work_item_id == item_id)
        )
        return set(rows.scalars().all())


def _issue(item_key: str, action: str) -> dict[str, Any]:
    number = int(item_key.rsplit("#", 1)[1].split("-", 1)[0])
    return {
        "action": action,
        "repository": REPO,
        "issue": {"number": number, "title": "Credit notes", "state": "open", "labels": []},
        "sender": {"login": "marie"},
    }


async def _rekey(item_id: str, key: str) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, item_id)
        assert item is not None
        item.tracker_key = key


async def test_an_ended_work_item_whose_issue_is_reopened_marks_its_decisions(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    name = await _workflow_name(client, project)
    (item_id,) = await _record(project, name, [("route", 0.9, True)], closed=True)
    await _rekey(item_id, KEY)
    await _post(client, "r-1", _issue(KEY, "edited"))
    assert await _reopened(item_id) == {False}, "an edit is not a reopening"
    await _post(client, "r-2", _issue(KEY, "reopened"))
    assert await _reopened(item_id) == {True}


async def test_a_running_work_item_is_not_reopened(client: AsyncClient, project: dict[str, Any]) -> None:
    name = await _workflow_name(client, project)
    (item_id,) = await _record(project, name, [("route", 0.9, None)])
    await _rekey(item_id, KEY)
    await _post(client, "r-3", _issue(KEY, "reopened"))
    assert await _reopened(item_id) == {False}


@pytest.mark.parametrize(
    ("event_type", "payload", "reopened"),
    [
        ("tracker.item.updated", {"reopened": True}, True),
        ("tracker.item.updated", {"state": "open"}, False),
        ("tracker.item.moved", {"from_status": "Done", "to_status": "In Progress"}, True),
        ("tracker.item.moved", {"from_status": "done ", "to_status": "To do"}, True),  # case-blind
        ("tracker.item.moved", {"from_status": "In Progress", "to_status": "Done"}, False),  # the end
        ("tracker.item.moved", {"from_status": "Done", "to_status": "Cancelled"}, False),  # still ended
        ("tracker.item.moved", {"from_status": "Review", "to_status": "In Progress"}, False),
        ("tracker.item.moved", {"to_status": "In Progress"}, False),
        ("tracker.item.commented", {"reopened": True}, False),
    ],
)
def test_which_tracker_events_say_an_ended_issue_is_open_again(
    event_type: str, payload: dict[str, Any], reopened: bool
) -> None:
    from choregos_api.services.decision_ledger import tracker_says_reopened

    assert tracker_says_reopened(event_type, payload, ["Done", "Cancelled"]) is reopened

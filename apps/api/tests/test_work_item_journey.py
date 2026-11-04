"""The journey of a work item reads in one go, and each move names its edge (S22-01).

The console animated the life of a work item by rereading the titles of its timeline ("a -> b"):
nothing said by which transition it had gone, nor whether a review had sent it back or a person
refused it. The route `/work-items/{id}/journey` returns the map of the version it is pinned to,
its moves attached to their edge, and the steps of each actor: runs, requests, actions, train
departures.
"""

from __future__ import annotations

import pathlib
from datetime import timedelta
from typing import Any

import pytest
from choregos_core import parse_workflow, utcnow
from httpx import AsyncClient

from .conftest import login

ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def dev_complex() -> Any:
    text = (ROOT / "templates/github-software-delivery/workflows/dev-complex.yaml").read_text()
    workflow, _ = parse_workflow(text)
    return workflow


@pytest.mark.parametrize(
    ("source", "target", "reason", "expected"),
    [
        (None, "inbox", "started", ("start", None)),
        ("implemented", "tested", "", ("nominal", "t-test")),
        ("implemented", "planned", "failing gates: evidence_present", ("retry", "t-test")),
        ("documented", "addressing_review", "", ("changes_requested", "t-review")),
        ("reviewed", "addressing_review", "", ("changes_requested", "t-security-review")),
        ("addressing_review", "implemented", "", ("nominal", "t-address-review")),
        (
            "awaiting_pr_approval",
            "addressing_review",
            "human decision: sent back",
            ("reject", "t-approve-pr"),
        ),
        ("pr_approved", "fixing_ci", "failing gates: ci_green", ("retry", "t-merge")),
        ("planned", "needs_human", "3 attempts exhausted", ("escalate", "t-implement")),
        ("integrated", "staged", "deployment verified", ("nominal", "t-stage")),
        ("needs_human", "planned", "answered", ("resume", None)),
        ("needs_human", "abandoned", "", ("nominal", "t-abandon")),
        ("planned", "implemented", "migrated to dev-complex@2", ("migrated", None)),
        ("inbox", "verified", "moved by hand in the tracker", ("other", None)),
    ],
)
def test_each_move_names_the_edge_that_carried_it(
    dev_complex: Any, source: str | None, target: str, reason: str, expected: tuple[str, str | None]
) -> None:
    from choregos_api.services import move_edge

    assert move_edge(dev_complex, source, target, reason) == expected


def test_a_default_from_any_agent_state_is_recognised(dev_complex: Any) -> None:
    from choregos_api.services import move_edge

    # `documented` has no escalation to `needs_human` on its transition: it is the `on_question` default.
    without_escalation = dev_complex.model_copy(deep=True)
    for t in without_escalation.transitions:
        if t.key == "t-review":
            t.on_changes_requested = None
    assert move_edge(without_escalation, "documented", "needs_human", "a question") == ("default", None)


DELIVERY = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: delivery, version: 1}
initial: inbox
actors:
  dev: {type: agent, role: implement}
  reviewer: {type: agent, role: review, fresh_context: true}
  responder: {type: agent, role: address_review}
  maintainers: {type: human, group: maintainers}
  platform: {type: system}
states:
  inbox: {display: To do, kind: wait}
  implemented: {display: Implemented}
  reviewed: {display: Reviewed}
  addressing: {display: Addressing the review}
  approved: {display: Approved}
  staged: {display: In staging}
  done: {display: Done, terminal: true}
  needs_human: {display: Needs a human, kind: wait}
transitions:
  - id: t-impl
    from: inbox
    to: implemented
    by: dev
    on_fail: {to: inbox, max_attempts: 2, escalate_to: needs_human}
  - id: t-review
    from: implemented
    to: reviewed
    by: reviewer
    outputs: [verdict]
    on_changes_requested: {to: addressing, max_attempts: 2, escalate_to: needs_human}
  - id: t-address
    from: addressing
    to: implemented
    by: responder
    on_fail: {to: addressing, max_attempts: 2, escalate_to: needs_human}
  - {id: t-approve, from: reviewed, to: approved, by: maintainers, on_reject: addressing}
  - {id: t-stage, from: approved, to: staged, via: release_train, train: {env: staging}}
  - {id: t-close, from: staged, to: done, by: platform}
defaults:
  from_any_agent_state: {on_question: needs_human}
  needs_human: {on_abandon: done}
"""


async def _work_item(client: AsyncClient, pid: str) -> dict[str, Any]:
    published = await client.put(f"/api/v1/projects/{pid}/workflow", json={"yaml": DELIVERY})
    assert published.status_code == 200, published.text
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "The credit notes", "start": False}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _life_of_the_work_item(project: dict[str, Any], item: dict[str, Any]) -> dict[str, str]:
    """A complete life, written as the orchestrator writes it: a review that sends back once, a human
    approval, a train departure, and the platform action that is still waiting."""
    from choregos_api.db.models import Action, Event, HumanRequest, Project, Release, Run
    from choregos_api.db.session import session_scope

    begin = utcnow() - timedelta(hours=3)
    instant = iter(begin + timedelta(minutes=10 * i) for i in range(1, 40))
    ids: dict[str, str] = {}

    async with session_scope(orgs="*") as session:
        project_row = await session.get(Project, project["id"])
        assert project_row is not None

        def move(source: str | None, target: str, reason: str = "") -> None:
            session.add(
                Event(
                    project_id=project["id"],
                    work_item_id=item["id"],
                    type="choregos.workitem.state_changed",
                    subject=item["tracker_key"],
                    payload={"from": source, "to": target, "reason": reason},
                    ts=next(instant),
                )
            )

        def run_stage(transition: str, role: str, attempt_no: int, verdict: str | None = None) -> Run:
            started = next(instant)
            run = Run(
                work_item_id=item["id"],
                project_id=project["id"],
                transition_id=transition,
                stage_role=role,
                attempt=attempt_no,
                actor={"t-impl": "dev", "t-review": "reviewer", "t-address": "responder"}[transition],
                model="profile:standard",
                status="succeeded",
                started_at=started,
                ended_at=started + timedelta(minutes=4),
                cost_usd=0.5,
                result={
                    "summary": f"{role}, round {attempt_no}",
                    "outputs": {"verdict": verdict} if verdict else {},
                    "evidence": {"tests_run": 412, "tests_failed": 0, "lint": "ok", "coverage_delta": None},
                },
            )
            session.add(run)
            return run

        move(None, "inbox", "started")
        run_stage("t-impl", "implement", 1)
        move("inbox", "implemented", "credit notes deducted")
        review_run = run_stage("t-review", "review", 1, "changes_requested")
        move("implemented", "addressing", "two remarks")
        run_stage("t-address", "address_review", 1)
        move("addressing", "implemented")
        run_stage("t-review", "review", 2, "approve")
        move("implemented", "reviewed")
        request = HumanRequest(
            work_item_id=item["id"],
            project_id=project["id"],
            transition_id="t-approve",
            kind="approval",
            payload={"summary": "Approve the pull request"},
            requested_at=next(instant),
            decided_at=next(instant),
            decided_by="marie@varga.dev",
            decision={"kind": "approval", "approved": True, "reason": "looks good"},
        )
        session.add(request)
        move("reviewed", "approved", "human decision: approved")
        release = Release(
            project_id=project["id"],
            env="staging",
            batch_no=7,
            status="verified",
            items=[{"work_item_key": item["tracker_key"], "title": item["title"]}],
            started_at=next(instant),
            ended_at=next(instant),
        )
        session.add(release)
        # A release that does not carry it is not a step of this work item.
        session.add(
            Release(
                project_id=project["id"],
                env="staging",
                batch_no=8,
                status="verified",
                items=[{"work_item_key": "OTHER-9"}],
                started_at=next(instant),
            )
        )
        move("approved", "staged", "deployment verified")
        action = Action(
            org_id=project_row.org_id,
            project_id=project["id"],
            work_item_id=item["id"],
            origin="transition",
            kind="close.ticket",
            title="Close the work item",
            proposed_by={"kind": "system", "id": "system", "via": "transition", "transition": "t-close"},
            status="pending_approval",
        )
        session.add(action)
        await session.flush()
        ids["review-1"], ids["request"], ids["release"], ids["action"] = (
            review_run.id,
            request.id,
            release.id,
            action.id,
        )
    return ids


async def test_the_journey_attaches_each_move_and_each_step_to_its_transition(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _work_item(client, project["id"])
    ids = await _life_of_the_work_item(project, item)

    response = await client.get(f"/api/v1/work-items/{item['id']}/journey")
    assert response.status_code == 200, response.text
    journey = response.json()

    assert (journey["workflow_name"], journey["workflow_version"], journey["initial"]) == (
        "delivery",
        1,
        "inbox",
    )
    assert {n["id"] for n in journey["graph"]["nodes"]} >= {"inbox", "addressing", "staged"}
    assert [e["id"] for e in journey["process"]][:2] == ["t-impl", "t-review"]
    assert [(m["from"], m["to"], m["kind"], m["transition_id"]) for m in journey["moves"]] == [
        (None, "inbox", "start", None),
        ("inbox", "implemented", "nominal", "t-impl"),
        ("implemented", "addressing", "changes_requested", "t-review"),
        ("addressing", "implemented", "nominal", "t-address"),
        ("implemented", "reviewed", "nominal", "t-review"),
        ("reviewed", "approved", "nominal", "t-approve"),
        ("approved", "staged", "nominal", "t-stage"),
    ]

    steps = journey["steps"]
    assert [(p["kind"], p["transition_id"], p["attempt"]) for p in steps] == [
        ("agent", "t-impl", 1),
        ("agent", "t-review", 1),
        ("agent", "t-address", 1),
        ("agent", "t-review", 2),
        ("human", "t-approve", 1),
        ("action", "t-stage", 1),
        ("action", "t-close", 1),
    ], "in time order, minus the release that does not carry it"
    reviews = [p for p in steps if p["transition_id"] == "t-review"]
    assert [r["verdict"] for r in reviews] == ["changes_requested", "approve"]
    assert reviews[0]["id"] == ids["review-1"] and reviews[1]["summary"] == "review, round 2"
    assert reviews[0]["evidence"] == {"tests_run": 412, "tests_failed": 0, "lint": "ok"}, (
        "null evidence stays silent"
    )
    person = next(p for p in steps if p["kind"] == "human")
    assert (person["id"], person["actor"], person["status"], person["decided_by"]) == (
        ids["request"],
        "maintainers",
        "approved",
        "marie@varga.dev",
    )
    train = next(p for p in steps if p["transition_id"] == "t-stage")
    assert (train["id"], train["status"], train["actor"]) == (ids["release"], "verified", "release_train")
    waiting = steps[-1]
    assert (waiting["id"], waiting["status"], waiting["actor"]) == (
        ids["action"],
        "pending_approval",
        "platform",
    )
    assert (journey["state"], journey["closed"]) == (item["state"], False)


async def test_the_journey_keeps_the_map_where_the_work_item_is_pinned(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _work_item(client, project["id"])
    from choregos_core import template_yaml

    other = await client.put(
        f"/api/v1/projects/{project['id']}/workflow", json={"yaml": template_yaml("default-simple")}
    )
    assert other.status_code == 200, other.text
    journey = (await client.get(f"/api/v1/work-items/{item['id']}/journey")).json()
    assert journey["workflow_name"] == "delivery"
    assert "addressing" in {n["id"] for n in journey["graph"]["nodes"]}, (
        "the map of the work item, not that of the project"
    )


async def test_a_pending_human_request_shows_and_a_stranger_reads_nothing(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope

    item = await _work_item(client, project["id"])
    async with session_scope(orgs="*") as session:
        session.add(
            HumanRequest(
                work_item_id=item["id"],
                project_id=project["id"],
                transition_id="t-approve",
                kind="approval",
                payload={"question": "Ship it?"},
                requested_at=utcnow(),
                due_at=utcnow() + timedelta(hours=24),
            )
        )
    (steps,) = (await client.get(f"/api/v1/work-items/{item['id']}/journey")).json()["steps"]
    assert (steps["status"], steps["summary"], steps["ended_at"]) == ("waiting", "Ship it?", None)
    assert steps["due_at"] is not None

    assert (await client.get("/api/v1/work-items/unknown/journey")).status_code == 404
    # An account with no role in the organisation: the development login sets one, we remove it.
    await login(client, "intruder@elsewhere.dev")
    from choregos_api.db.models import Membership, User
    from sqlalchemy import delete, select

    async with session_scope(orgs="*") as session:
        intruder = (
            await session.execute(select(User).where(User.email == "intruder@elsewhere.dev"))
        ).scalar_one()
        await session.execute(delete(Membership).where(Membership.user_id == intruder.id))
    # On PostgreSQL, RLS already hides the work item from this account (404); on SQLite, the route
    # guard answers (403). Both say the same thing: a stranger reads nothing, never 200.
    assert (await client.get(f"/api/v1/work-items/{item['id']}/journey")).status_code in {403, 404}

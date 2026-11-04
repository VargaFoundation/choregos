"""Functional journeys of the API: projects, workflow, RBAC, decisions, webhooks, internal API."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login


async def test_me_requires_session(client: AsyncClient) -> None:
    response = await client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "Not authenticated"


async def test_login_and_me(client: AsyncClient, org: str) -> None:
    await login(client, "admin@varga.dev")
    me = (await client.get("/api/v1/me")).json()
    assert me["email"] == "admin@varga.dev"
    assert me["memberships"][0]["role"] == "org_admin"


async def test_project_creation_sets_defaults(client: AsyncClient, project: dict[str, Any]) -> None:
    assert project["status"] == "draft"
    workflow = (await client.get(f"/api/v1/projects/{project['id']}/workflow")).json()
    assert workflow["name"] == "default-simple"
    policy = (await client.get(f"/api/v1/projects/{project['id']}/policy")).json()
    assert policy["name"] == "solo"


async def test_workflow_validation_reports_line_and_column(client: AsyncClient, admin: str) -> None:
    bad = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: t, version: 1 }
actors: { dev: { type: agent, role: implement } }
states:
  a: { display: A }
  b: { display: B, terminal: true }
transitions:
  - { from: a, to: nulle_part, by: dev }
"""
    response = await client.post("/api/v1/workflows/validate", json={"yaml": bad})
    assert response.status_code == 200
    body = response.json()
    assert not body["valid"]
    error = next(e for e in body["errors"] if e["code"] == "state.unknown")
    assert error["line"] and error["path"].startswith("transitions[0]")


async def test_put_invalid_workflow_is_422(client: AsyncClient, project: dict[str, Any]) -> None:
    response = await client.put(
        f"/api/v1/projects/{project['id']}/workflow",
        json={"yaml": "apiVersion: choregos/v1\nkind: Workflow\nmetadata: {name: x, version: 1}\n"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_put_valid_workflow_bumps_version(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_core.dsl import template_yaml

    response = await client.put(
        f"/api/v1/projects/{project['id']}/workflow", json={"yaml": template_yaml("advanced")}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "advanced"
    current = (await client.get(f"/api/v1/projects/{project['id']}/workflow")).json()
    assert current["name"] == "advanced"


async def test_rbac_developer_cannot_write_workflow(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org_row = (await session.execute(select(Organization))).scalars().first()
        user = User(email="dev@varga.dev", display_name="dev")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org_row.id, role="developer"))

    async with AsyncClient(transport=client._transport, base_url="http://test") as dev_client:
        await login(dev_client, "dev@varga.dev")
        response = await dev_client.put(f"/api/v1/projects/{project['id']}/workflow", json={"yaml": "x: 1"})
        assert response.status_code == 403


async def test_connector_crud_and_test(client: AsyncClient, project: dict[str, Any]) -> None:
    response = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={"type": "github-issues", "config": {"repo": "varga/billing-api"}},
    )
    assert response.status_code == 200
    listed = (await client.get(f"/api/v1/projects/{project['id']}/connectors")).json()
    assert listed[0]["kind"] == "tracker"
    tested = (await client.post(f"/api/v1/projects/{project['id']}/connectors/tracker/test")).json()
    assert tested["ok"] is True
    types = (await client.get("/api/v1/connectors/types")).json()
    assert any(t["type"] == "github-issues" for t in types)


async def test_github_webhook_signature_and_dedup(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.config import get_settings

    settings = get_settings()
    settings.github_webhook_secret = "s3cr3t"
    payload = {
        "action": "labeled",
        "repository": {"full_name": "varga/billing-api"},
        "issue": {
            "number": 42,
            "title": "Fix the credit notes",
            "body": "…",
            "labels": [{"name": "agent-ready"}],
        },
        "label": {"name": "agent-ready"},
        "sender": {"login": "augustin"},
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
    headers = {
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": signature,
        "content-type": "application/json",
    }
    first = await client.post("/api/v1/webhooks/github", content=body, headers=headers)
    assert first.status_code == 202, first.text
    assert first.json()["events"] == 1

    duplicate = await client.post("/api/v1/webhooks/github", content=body, headers=headers)
    assert duplicate.json()["duplicate"] is True

    bad = await client.post(
        "/api/v1/webhooks/github", content=body, headers={**headers, "X-Hub-Signature-256": "sha256=deadbeef"}
    )
    assert bad.status_code == 401
    settings.github_webhook_secret = ""


async def test_webhook_starts_one_workflow_only(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.temporal import get_temporal

    payload = {
        "action": "labeled",
        "repository": {"full_name": "varga/billing-api"},
        "issue": {"number": 7, "title": "T", "labels": [{"name": "agent-ready"}]},
        "label": {"name": "agent-ready"},
        "sender": {"login": "a"},
    }
    body = json.dumps(payload).encode()
    for delivery in ("d1", "d2"):
        await client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "issues",
                "X-GitHub-Delivery": delivery,
                "content-type": "application/json",
            },
        )
    started = get_temporal().started  # type: ignore[attr-defined]
    assert len([k for k in started if k.startswith("wi-billing-api")]) == 1


async def test_internal_run_api(client: AsyncClient, project: dict[str, Any]) -> None:
    """The runner reads its input, logs, posts findings and a result, and nothing else."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from choregos_contracts import StageInput

    stage_input = StageInput.model_validate(
        json.loads(
            (
                __import__("pathlib").Path(
                    __import__("choregos_contracts").schemas_dir() / "examples" / "stage-input.example.json"
                )
            ).read_text()
        )
    )
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/billing-api#1", title="T", state="ready")
        session.add(item)
        await session.flush()
        run = Run(
            id=stage_input.run_id,
            work_item_id=item.id,
            project_id=project["id"],
            stage_role="implement",
            transition_id="t-implement",
            status="running",
            stage_input=stage_input.model_dump(mode="json", by_alias=True),
            allowed_paths=["src/orders/**"],
        )
        session.add(run)
        run_id = run.id

    token = mint_run_token(
        run_id, project_slug="billing-api", work_item_key="varga/billing-api#1", ttl_minutes=30
    )
    headers = {"Authorization": f"Bearer {token}"}

    fetched = await client.get(f"/api/v1/internal/runs/{run_id}/input", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["run_id"] == run_id

    other = await client.get("/api/v1/internal/runs/other-run/input", headers=headers)
    assert other.status_code == 403

    events = await client.post(
        f"/api/v1/internal/runs/{run_id}/events",
        headers=headers,
        json={"events": [{"seq": 1, "type": "session/update", "payload": {"text": "…"}}]},
    )
    assert events.json()["accepted"] == 1
    again = await client.post(
        f"/api/v1/internal/runs/{run_id}/events",
        headers=headers,
        json={"events": [{"seq": 1, "type": "session/update", "payload": {"text": "…"}}]},
    )
    assert again.json()["accepted"] == 0, "the log is idempotent by seq"

    finding = await client.post(
        f"/api/v1/internal/runs/{run_id}/findings",
        headers=headers,
        json={"title": "N+1 on the lines", "type": "perf", "severity": "medium", "evidence": "repo.py:88"},
    )
    assert finding.status_code == 201
    assert finding.json()["accepted"] is True

    scope = await client.post(
        f"/api/v1/internal/runs/{run_id}/scope-change",
        headers=headers,
        json={"paths": ["src/shared/util.py"], "justification": "shared function"},
    )
    assert scope.json()["decision"] == "granted"

    denied = await client.post(
        f"/api/v1/internal/runs/{run_id}/scope-change",
        headers=headers,
        json={"paths": [".choregos/workflow.yaml"], "justification": "no"},
    )
    assert denied.json()["decision"] == "denied"

    result = {
        "schema": "choregos/StageResult/v1",
        "status": "done",
        "summary": "done",
        "evidence": {"tests_passed": True, "tests_run": 12},
        "outputs": {"size": "M"},
    }
    posted = await client.post(f"/api/v1/internal/runs/{run_id}/result", headers=headers, json=result)
    assert posted.json()["status"] == "recorded"
    reposted = await client.post(f"/api/v1/internal/runs/{run_id}/result", headers=headers, json=result)
    assert reposted.json()["status"] == "already_recorded", "posting the result is idempotent"

    after = await client.get(f"/api/v1/internal/runs/{run_id}/input", headers=headers)
    assert after.status_code == 409, "result already posted: the runner must exit with 0"


async def test_internal_requires_valid_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/internal/runs/x/input")
    assert response.status_code == 401
    response = await client.get("/api/v1/internal/runs/x/input", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 403


async def test_findings_listing_and_triage(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import Finding
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        row = Finding(
            project_id=project["id"],
            title="Connection leak",
            type="bug",
            severity="high",
            evidence="db.py:12",
            status="pending",
        )
        session.add(row)
        await session.flush()
        finding_id = row.id

    listed = (await client.get(f"/api/v1/projects/{project['id']}/findings")).json()
    assert listed["items"][0]["title"] == "Connection leak"

    dismissed = await client.post(f"/api/v1/findings/{finding_id}/actions", json={"action": "dismiss"})
    assert dismissed.json()["status"] == "dismissed"


async def test_costs_report(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        for index in range(3):
            session.add(
                CostLedger(
                    project_id=project["id"],
                    provider="anthropic",
                    model="platform/standard",
                    stage_role="implement" if index else "refine",
                    size="M",
                    tokens_in=1000,
                    tokens_out=100,
                    cost_usd=0.5,
                    cost_eur=0.46,
                    fx_rate=0.92,
                    ts=utcnow(),
                )
            )

    by_stage = (
        await client.get(f"/api/v1/projects/{project['id']}/costs", params={"group_by": "stage"})
    ).json()
    assert {row["key"] for row in by_stage["rows"]} == {"implement", "refine"}
    assert by_stage["total"]["cost_usd"] == pytest.approx(1.5)


async def test_audit_trail_records_mutations(client: AsyncClient, project: dict[str, Any]) -> None:
    audit = (await client.get("/api/v1/audit")).json()
    actions = {entry["action"] for entry in audit["items"]}
    assert "project.create" in actions


async def test_templates_listing_from_disk(client: AsyncClient, admin: str) -> None:
    listed = (await client.get("/api/v1/templates")).json()
    assert isinstance(listed, list)


async def test_platform_backends_and_executors(client: AsyncClient, admin: str) -> None:
    backends = (await client.get("/api/v1/platform/backends")).json()
    assert {b["name"] for b in backends} >= {"codex", "claude-code"}
    assert next(b for b in backends if b["name"] == "claude-code")["enabled"] is True
    assert "openhands" not in {b["name"] for b in backends}
    updated = await client.put(
        "/api/v1/platform/backends",
        json={"name": "codex", "enabled": False, "disabled_reason": "conformance red"},
    )
    assert updated.json()["enabled"] is False
    executors = (await client.get("/api/v1/platform/executors")).json()
    assert {e["kind"] for e in executors} >= {"tekton", "k8s_job"}


async def test_a_jira_webhook_creates_the_work_item_and_starts_the_workflow(
    client: AsyncClient, project: dict[str, Any], monkeypatch: Any
) -> None:
    """A Jira webhook follows exactly the same path as a GitHub webhook (S13-04)."""
    from choregos_api.config import get_settings

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", "s3cret")
    payload = {
        "webhookEvent": "jira:issue_updated",
        "user": {"displayName": "Augustin"},
        "issue": {"key": "BILLING-API-9", "fields": {"summary": "Credit notes", "labels": ["agent-ready"]}},
        "changelog": {"items": [{"field": "labels", "fromString": "", "toString": "agent-ready"}]},
    }
    response = await client.post(
        "/api/v1/webhooks/jira",
        json=payload,
        headers={"X-Choregos-Secret": "s3cret", "X-Atlassian-Webhook-Identifier": "d-1"},
    )
    assert response.status_code == 202
    assert response.json()["accepted"] is True

    replayed = await client.post(
        "/api/v1/webhooks/jira",
        json=payload,
        headers={"X-Choregos-Secret": "s3cret", "X-Atlassian-Webhook-Identifier": "d-1"},
    )
    assert replayed.json()["duplicate"] is True, "a replayed delivery counts only once"


async def test_a_tracker_birth_records_who_filed_the_issue(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The issue's author is the work item's reporter (#1179), not the webhook's sender."""
    payload = {
        "action": "labeled",
        "repository": {"full_name": "varga/billing-api"},
        "issue": {
            "number": 11,
            "title": "Access to billing",
            "user": {"login": "jmartin"},
            "labels": [{"name": "agent-ready"}],
        },
        "label": {"name": "agent-ready"},
        "sender": {"login": "triage-bot"},
    }
    response = await client.post(
        "/api/v1/webhooks/github",
        content=json.dumps(payload).encode(),
        headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-11", "content-type": "application/json"},
    )
    assert response.status_code == 202, response.text

    listing = (await client.get(f"/api/v1/projects/{project['id']}/work-items")).json()
    born = next(i for i in listing["items"] if i["tracker_key"].endswith("#11"))
    assert born["reporter"] == "jmartin"
    assert (await client.get(f"/api/v1/work-items/{born['id']}")).json()["reporter"] == "jmartin"


async def test_a_gitlab_webhook_refuses_an_invalid_token(
    client: AsyncClient, project: dict[str, Any], monkeypatch: Any
) -> None:
    from choregos_api.config import get_settings

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", "s3cret")
    response = await client.post(
        "/api/v1/webhooks/gitlab",
        json={"object_kind": "issue"},
        headers={"X-Gitlab-Token": "wrong", "X-Gitlab-Event": "Issue Hook"},
    )
    assert response.status_code == 401


async def test_a_project_without_a_repository_is_created(client: AsyncClient, admin: str) -> None:
    """Limit no. 1 of ADR 0012: a trade that processes case files has no repository, and yet had to
    declare one that served no purpose."""
    response = await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": "staffing",
            "name": "Staffing",
            "config": {"slug": "staffing", "org": "varga"},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["config"].get("repo") is None

    from choregos_contracts import ProjectConfig

    config = ProjectConfig.model_validate(response.json()["config"])
    assert not config.has_repo
    with pytest.raises(ValueError, match="has no repository"):
        _ = config.repo_url


async def test_the_access_sheet_folds_the_log(
    client: AsyncClient, project: dict[str, Any], admin: str
) -> None:
    """Two agents produce two hundred events in a few minutes: nobody reads them. The sheet answers
    "what did it touch", and puts the refusals first."""
    from choregos_api.db.models import Run, RunEvent, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#7", title="T", state="ready")
        session.add(item)
        await session.flush()
        run = Run(
            id="run-access-1",
            work_item_id=item.id,
            project_id=project["id"],
            stage_role="implement",
            status="succeeded",
        )
        session.add(run)
        events_list = [
            ("session/update", {"update": {"text": "I read the work item"}}),
            (
                "session/request_permission",
                {"kind": "read", "target": "/workspace/src/a.py", "allowed": True, "reason": "ok"},
            ),
            (
                "session/request_permission",
                {"kind": "read", "target": "/workspace/src/a.py", "allowed": True, "reason": "ok"},
            ),
            (
                "session/request_permission",
                {
                    "kind": "write",
                    "target": "/workspace/.env",
                    "allowed": False,
                    "reason": "sensitive file",
                },
            ),
        ]
        from choregos_core import utcnow

        for seq, (type_, payload) in enumerate(events_list, start=1):
            session.add(RunEvent(run_id=run.id, seq=seq, type=type_, payload=payload, ts=utcnow()))

    response = await client.get("/api/v1/runs/run-access-1/access")
    assert response.status_code == 200, response.text
    sheet = response.json()
    assert sheet["events"] == 4 and sheet["refusals"] == 1
    assert sheet["access"][0]["target"] == "/workspace/.env", "the refusal first"
    assert sheet["access"][0]["reasons"] == ["sensitive file"]
    assert sheet["access"][1]["requests"] == 2, "two reads of the same file, grouped"
    # The agent's prose does not enter it: the sheet would become unreadable again.
    assert all("I read the work item" not in a["target"] for a in sheet["access"])


async def test_a_work_item_whose_workflow_is_dead_says_so(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The API reads the Temporal state AND the mark set by the workflow: a dead work item no longer hides.

    Bench of 2026-09-24: two FAILED workflows, two "waiting" work items on the screen.
    """
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import FakeTemporal, WorkflowState, get_temporal

    async with session_scope() as session:
        item = WorkItem(
            project_id=project["id"],
            tracker_key="varga/billing-api#404",
            title="T",
            state="ready",
            temporal_wf_id="wi-billing-api-404",
            failure={"message": "the project has no repository", "activity": "collect_run_artifacts"},
        )
        session.add(item)
        await session.flush()
        item_id = item.id
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    fake.described["wi-billing-api-404"] = WorkflowState(status="FAILED", failure="Activity task failed")

    body = (await client.get(f"/api/v1/work-items/{item_id}")).json()
    assert body["workflow_status"] == "FAILED"
    assert body["failure"]["message"] == "the project has no repository"
    assert body["failure"]["activity"] == "collect_run_artifacts"

    # The list asks Temporal nothing (one call per work item would cost too much): the status is
    # absent there, but the persisted mark is present.
    listing = (await client.get(f"/api/v1/projects/{project['id']}/work-items")).json()
    dead = next(i for i in listing["items"] if i["id"] == item_id)
    assert dead["workflow_status"] is None
    assert dead["failure"]["message"] == "the project has no repository"


async def test_a_declined_ticket_says_so_and_is_not_a_success(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A terminal state's declared outcome reaches the ticket and the board: a declined access
    request in green read as granted (the Fondation access workflow, 2026-11-02)."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    yaml_text = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: access, version: 1 }
actors:
  analyst: { type: agent, role: analyse }
  fondation: { type: human, group: fondation }
states:
  inbox: { display: To take, kind: wait }
  to_apply: { display: To apply, kind: wait }
  applied: { display: Applied, terminal: true }
  declined: { display: Declined by Fondation, terminal: true, outcome: declined }
transitions:
  - { id: t-analyse, from: inbox, to: to_apply, by: analyst }
  - { id: t-apply, from: to_apply, to: applied, by: fondation, on_reject: declined }
"""
    response = await client.put(f"/api/v1/projects/{project['id']}/workflow", json={"yaml": yaml_text})
    assert response.status_code == 200, response.text

    async with session_scope() as session:
        ids = {}
        for state in ("declined", "applied", "to_apply"):
            item = WorkItem(
                project_id=project["id"], tracker_key=f"varga/billing-api#{state}", title="T", state=state
            )
            session.add(item)
            await session.flush()
            ids[state] = item.id

    outcomes = {
        state: (await client.get(f"/api/v1/work-items/{item_id}")).json()["state_outcome"]
        for state, item_id in ids.items()
    }
    assert outcomes == {"declined": "declined", "applied": "success", "to_apply": None}

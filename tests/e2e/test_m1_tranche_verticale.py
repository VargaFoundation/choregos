"""M1 — the vertical slice: from an `agent-ready` issue to a green PR, cost in the ticket.

Plan scenario (§7.3): a GitHub issue labelled `agent-ready` starts a
`WorkflowInterpreter`, a stage runs, a PR opens, the cost comment is written
in the issue, a provoked finding becomes a linked ticket.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import pytest
from choregos_contracts import Evidence, StageResult, StageStatus

from .conftest import Platform, login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def evidence() -> Evidence:
    return Evidence(
        tests_passed=True, tests_run=412, tests_failed=0, coverage_delta=1.2, lint="ok", typecheck="ok"
    )


async def test_issue_agent_ready_devient_une_pr_verte(platform: Platform, worker: Any) -> None:
    await login(platform.client)

    # ── 1. an issue is labelled `agent-ready` on GitHub ──
    payload = {
        "action": "labeled",
        "repository": {"full_name": "varga/billing-api"},
        "issue": {
            "number": 123,
            "title": "Credit notes are not deducted from the total",
            "body": "The displayed total ignores the credit note.",
            "labels": [{"name": "agent-ready"}],
        },
        "label": {"name": "agent-ready"},
        "sender": {"login": "augustin"},
    }
    body = json.dumps(payload).encode()
    response = await platform.client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "m1-1",
            "content-type": "application/json",
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["events"] == 1

    item = await platform.work_item("varga/billing-api#123")
    assert item is not None, "the webhook created the ticket"
    assert item.temporal_wf_id, "a WorkflowInterpreter was started (deterministic ID)"

    # ── 2. the orchestrator runs the workflow, the agents produce their results ──
    platform.adapters.tracker.items.setdefault(
        "varga/billing-api#123", _fake_tracker_item("varga/billing-api#123", item.title)
    )
    platform.adapters.scm.set_diff(
        "varga/billing-api",
        "main",
        "choregos/123",
        [("src/orders/total.py", 24, 6), ("tests/orders/test_total.py", 38, 0)],
    )
    for result in (
        StageResult(
            status=StageStatus.DONE,
            summary="specification written",
            outputs={
                "size": "M",
                "risk": "low",
                "spec_markdown": "## Spec",
                "allowed_paths": ["src/orders/**", "tests/orders/**"],
            },
            evidence=evidence(),
        ),
        StageResult(
            status=StageStatus.DONE,
            summary="credit notes deducted from the total",
            artifacts={"branch": "choregos/123", "commits": ["fix(orders): credit notes"]},
            evidence=evidence(),
            findings=[
                {
                    "title": "N+1 query when loading the lines",
                    "type": "perf",
                    "severity": "medium",
                    "evidence": "src/orders/repository.py:88",
                    "estimate": "S",
                }
            ],
        ),
        StageResult(status=StageStatus.DONE, summary="412 tests green", evidence=evidence()),
    ):
        platform.adapters.executor.queue_result(result)

    async with worker():
        handle = await platform.env.client.start_workflow(
            "WorkflowInterpreter",
            {
                "project_id": platform.project_id,
                "project_slug": platform.project_slug,
                "work_item_id": item.id,
                "tracker_key": item.tracker_key,
            },
            id=f"wi-{platform.project_slug}-123",
            task_queue="e2e",
        )
        await _wait_state(handle, "awaiting_spec_approval")
        await _wait_pending_request(platform, item.id)

        # ── 3. a human approves the specification from the API ──
        decision = await platform.client.post(
            f"/api/v1/work-items/{item.id}/decisions", json={"kind": "approve"}
        )
        assert decision.status_code == 202, decision.text
        await handle.signal(
            "human_decision", {"approved": True, "decided_by": "augustin@varga.dev", "kind": "approval"}
        )

        # ── 4. the PR opens, the CI and the review arrive ──
        await _wait_state(handle, "pr_open", timeout=90)
        ref = next(iter(platform.adapters.scm.prs.values())).ref
        platform.adapters.scm.set_checks(ref, "success")
        platform.adapters.scm.submit_review(ref, "marie", "approved")
        await handle.signal(
            "inbound",
            {
                "type": "scm.check.completed",
                "source": "github",
                "delivery_id": "m1-2",
                "payload": {"conclusion": "success"},
            },
        )
        await _wait_state(handle, "merged", timeout=90)

        # ── 5. the train deploys, Argo confirms ──
        await handle.signal(
            "inbound",
            {
                "type": "cd.rollout.completed",
                "source": "argocd",
                "delivery_id": "m1-3",
                "payload": {"env": "prod"},
            },
        )
        outcome = await handle.result()
        assert outcome["state"] == "deployed_prod"

    # ── 6. what the platform must have produced ──
    comment = platform.adapters.tracker.status_comment("varga/billing-api#123")
    assert comment is not None
    assert "Choregos — progress" in comment
    assert "agent refine" in comment and "agent implement" in comment
    assert "€" in comment, "the cost is written in the ticket"

    runs = (await platform.client.get(f"/api/v1/work-items/{item.id}/runs")).json()
    assert len(runs) >= 3
    assert all(run["cost_usd"] > 0 for run in runs), "every run has a cost counted at the gateway"

    findings = (await platform.client.get(f"/api/v1/projects/{platform.project_id}/findings")).json()
    assert findings["items"], "the finding filed by the agent is recorded"
    assert findings["items"][0]["severity"] == "medium"

    costs = (
        await platform.client.get(
            f"/api/v1/projects/{platform.project_id}/costs", params={"group_by": "stage"}
        )
    ).json()
    assert {row["key"] for row in costs["rows"]} >= {"refine", "implement"}

    updated = await platform.work_item("varga/billing-api#123")
    assert updated.pr_url, "a PR was opened"
    assert updated.state == "deployed_prod"
    assert updated.closed_at is not None, "the ticket is closed once in production"
    assert platform.adapters.tracker.items["varga/billing-api#123"].state == "Done"


async def test_webhook_signature_invalide_est_refusee(platform: Platform) -> None:
    from choregos_api.config import get_settings

    settings = get_settings()
    settings.github_webhook_secret = "s3cr3t"
    body = json.dumps({"action": "opened", "repository": {"full_name": "varga/billing-api"}}).encode()
    good = "sha256=" + hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
    ok = await platform.client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "sig-1", "X-Hub-Signature-256": good},
    )
    assert ok.status_code == 202
    bad = await platform.client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "sig-2", "X-Hub-Signature-256": "sha256=x"},
    )
    assert bad.status_code == 401
    settings.github_webhook_secret = ""


def _fake_tracker_item(key: str, title: str) -> Any:
    from choregos_core.domain import WorkItemData

    return WorkItemData(key=key, title=title, state="Todo")


async def _wait_pending_request(platform: Platform, item_id: str, timeout: int = 30) -> None:
    """Wait for the human request to be created: it is the one the API decides."""
    for _ in range(timeout * 10):
        payload = (await platform.client.get(f"/api/v1/work-items/{item_id}")).json()
        if payload.get("pending_request"):
            return
        await asyncio.sleep(0.1)
    raise AssertionError("no human request created")


async def _wait_state(handle: Any, state: str, timeout: int = 60) -> None:
    for _ in range(timeout * 10):
        status = await handle.query("status")
        if status["state"] == state:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"state `{state}` never reached (current: {(await handle.query('status'))['state']})"
    )

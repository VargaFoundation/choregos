"""A signal to a project's memory loop never answers 500 (S51-02).

Nothing started `MemoryIngestion` (`mem-<slug>`) before S51-02, so `reimport` and an Alertmanager alert
signalled a workflow that did not exist: Temporal's NOT_FOUND came back as a 500, and Alertmanager
retried it for ever. The loop is now started then signalled; a loop that cannot be (its project was
archived and its loop completed) is a clear 409 for `reimport` and an alert not kept for the webhook,
and a policy without memory is said, not signalled.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_api.temporal import WorkflowState
from httpx import AsyncClient

ALERT = {
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "f-mem",
            "labels": {"alertname": "ErrorBudget", "severity": "page", "project": "billing-api"},
        }
    ]
}


async def _disable_memory(project_id: str) -> None:
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(PolicyDef).where(PolicyDef.project_id == project_id, PolicyDef.is_active.is_(True))
            )
        ).scalar_one()
        row.json_doc = {
            **row.json_doc,
            "memory": {**dict(row.json_doc.get("memory") or {}), "enabled": False},
        }


async def _signalled_alerts(project_id: str) -> int:
    from choregos_api.db.models import ReceivedEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy import func, select

    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ReceivedEvent)
                    .where(ReceivedEvent.project_id == project_id, ReceivedEvent.outcome == "signalled")
                )
            ).scalar()
            or 0
        )


async def test_reimport_starts_a_loop_that_does_not_run_then_signals_it(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import get_temporal

    get_temporal().gone.add("mem-billing-api")  # never started: Temporal answers NOT_FOUND
    response = await client.post(
        f"/api/v1/projects/{project['id']}/memory/reimport", json={"sources": ["adr"]}
    )
    assert response.status_code == 202, response.text
    assert get_temporal().started["mem-billing-api"] == {"project_slug": "billing-api"}
    assert ("mem-billing-api", "reimport", {"sources": ["adr"]}) in get_temporal().signals


async def test_reimport_of_a_completed_loop_is_a_clear_conflict(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import get_temporal

    get_temporal().gone.add("mem-billing-api")
    get_temporal().described["mem-billing-api"] = WorkflowState(status="COMPLETED")  # its id is not reused
    response = await client.post(f"/api/v1/projects/{project['id']}/memory/reimport", json={})
    assert response.status_code == 409, response.text
    assert "memory loop of project billing-api does not run" in response.text


async def test_reimport_under_a_policy_without_memory_is_a_clear_conflict(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import get_temporal

    await _disable_memory(project["id"])
    response = await client.post(f"/api/v1/projects/{project['id']}/memory/reimport", json={})
    assert response.status_code == 409, response.text
    assert "memory.enabled: false" in response.text
    assert not [s for s in get_temporal().signals if s[0] == "mem-billing-api"]


@pytest.mark.parametrize("case", ["missing", "completed", "disabled"])
async def test_an_alert_for_a_loop_that_cannot_take_it_is_acknowledged_not_500(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    from choregos_api.config import get_settings
    from choregos_api.temporal import get_temporal

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", "s3cret")
    get_temporal().gone.add("mem-billing-api")
    if case == "completed":
        get_temporal().described["mem-billing-api"] = WorkflowState(status="COMPLETED")
    if case == "disabled":
        await _disable_memory(project["id"])
    response = await client.post(
        "/api/v1/webhooks/alertmanager", json=ALERT, headers={"X-Choregos-Secret": "s3cret"}
    )
    assert response.status_code == 202, response.text
    alerted = [s for s in get_temporal().signals if s[:2] == ("mem-billing-api", "alert")]
    if case == "missing":
        assert len(alerted) == 1, "the loop is started, then the alert reaches it"
        assert await _signalled_alerts(project["id"]) == 1
    else:
        assert alerted == []
        assert await _signalled_alerts(project["id"]) == 0, "an alert no loop took is not `signalled`"

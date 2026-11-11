"""The provisioning state the API writes does not overwrite the one the workflow wrote (S62-13).

Found in production on 2026-11-11: a project from `plugin:diametral-delivery/diametral-project`, whose
template has no provisioning step on that platform, stayed `provisioning` forever. Its workflow ended in
660 ms and `finish_provisioning` wrote `succeeded` and `active`; the request that started it then committed
`running` and `provisioning` over them. The fake below finishes the provisioning inside `start`, as a fast
worker does, before the request returns.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


async def test_a_provisioning_that_ends_before_the_request_returns_stays_ended(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import ALL_ORGS, session_scope
    from choregos_api.temporal import get_temporal

    temporal = get_temporal()
    start = temporal.start_provisioning

    async def ends_at_once(workflow_id: str, payload: dict[str, Any]) -> str:
        started = await start(workflow_id, payload)
        async with session_scope(orgs=ALL_ORGS) as session:  # the worker's `finish_provisioning`
            row = await session.get(Project, payload["project_id"])
            assert row is not None
            row.provision_state = {**(row.provision_state or {}), "status": "succeeded", "current_step": None}
            row.status = "active"
        return started

    monkeypatch.setattr(temporal, "start_provisioning", ends_at_once)
    answer = await client.post(f"/api/v1/projects/{project['id']}/provision", json={})
    assert answer.status_code == 202, answer.text
    state = (await client.get(f"/api/v1/projects/{project['id']}/provision")).json()
    assert state["status"] == "succeeded", state
    assert (await client.get(f"/api/v1/projects/{project['id']}")).json()["status"] == "active"

"""An agent question and a scope change reach the inbox with an SLA date (S47-16)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import choregos_contracts
from httpx import AsyncClient


async def test_a_question_and_a_scope_change_carry_a_due_date(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import HumanRequest, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from choregos_contracts import StageInput
    from sqlalchemy import select

    example = Path(choregos_contracts.schemas_dir()) / "examples" / "stage-input.example.json"
    stage_input = StageInput.model_validate(json.loads(example.read_text()))
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

    scope = await client.post(
        f"/api/v1/internal/runs/{run_id}/scope-change",
        headers=headers,
        json={
            "paths": [f"db/migrations/{n:04d}.sql" for n in range(200)],
            "justification": "a migration is needed",
        },
    )
    assert scope.json()["decision"] == "pending"
    question = await client.post(
        f"/api/v1/internal/runs/{run_id}/question",
        headers=headers,
        json={"text": "Which table?", "options": ["a", "b"]},
    )
    assert question.status_code == 202

    async with session_scope() as session:
        result = await session.execute(select(HumanRequest).where(HumanRequest.project_id == project["id"]))
        rows = result.scalars().all()
    assert {r.kind for r in rows} == {"scope_change", "question"}
    for row in rows:
        assert row.due_at is not None, row.kind
        assert abs((row.due_at - row.requested_at) - timedelta(hours=24)) < timedelta(seconds=5)

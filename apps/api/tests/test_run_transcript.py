"""The run transcript is served from the journal the API stores (S36-01).

The runner used to announce `transcript.jsonl` next to the context pack in the object store,
and nothing uploaded it: `GET /runs/{id}/transcript` returned a link that led nowhere. A fake
run here does what a runner does through the internal API — batch its journal, post its
result — and the transcript must give its lines back.
"""

from __future__ import annotations

import json
from typing import Any

from httpx import AsyncClient


async def _running_run(project: dict[str, Any]) -> str:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        item = WorkItem(project_id=project["id"], tracker_key="ACME-7", title="A fix", state="planned")
        session.add(item)
        await session.flush()
        run = Run(
            work_item_id=item.id,
            project_id=project["id"],
            stage_role="implement",
            attempt=1,
            status="running",
            stage_input={},
        )
        session.add(run)
        await session.flush()
        return str(run.id)


def _run_token(run_id: str) -> dict[str, str]:
    from choregos_api.config import get_settings
    from choregos_api.security import mint_run_token

    token = mint_run_token(
        run_id, project_slug="billing-api", work_item_key="ACME-7", ttl_minutes=5, settings=get_settings()
    )
    return {"Authorization": f"Bearer {token}"}


async def test_a_finished_fake_run_gives_its_transcript_lines_back(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    run_id = await _running_run(project)
    headers = _run_token(run_id)
    journal = [
        {"seq": 1, "type": "run.started", "payload": {"run_id": run_id, "role": "implement"}},
        {"seq": 2, "type": "session/update", "payload": {"text": "reading src/a.py"}},
        {"seq": 3, "type": "run.result", "payload": {"status": "done", "summary": "ok"}},
    ]
    # Two batches, the second one replayed: the journal is idempotent by `seq`.
    for batch in (journal[:2], journal[1:], journal[1:]):
        posted = await client.post(
            f"/api/v1/internal/runs/{run_id}/events", json={"events": batch}, headers=headers
        )
        assert posted.status_code == 202, posted.text
    result = await client.post(
        f"/api/v1/internal/runs/{run_id}/result",
        json={"schema": "choregos/StageResult/v1", "status": "done", "summary": "ok"},
        headers=headers,
    )
    assert result.status_code == 200, result.text
    assert (await client.get(f"/api/v1/runs/{run_id}")).json()["status"] == "succeeded"

    response = await client.get(f"/api/v1/runs/{run_id}/transcript")
    assert response.status_code == 200, response.text
    ref = response.json()
    assert ref["content_type"] == "application/x-ndjson"
    lines = [json.loads(line) for line in ref["inline"].splitlines()]
    assert [(line["seq"], line["type"]) for line in lines] == [(e["seq"], e["type"]) for e in journal]
    assert lines[1]["payload"] == {"text": "reading src/a.py"}
    assert ref["size_bytes"] == len(ref["inline"].encode())
    # The URL is one the API answers, not an object-store path nothing wrote.
    linked = await client.get(ref["url"])
    assert linked.status_code == 200 and [e["seq"] for e in linked.json()] == [1, 2, 3]


async def test_a_run_without_journal_has_no_transcript(client: AsyncClient, project: dict[str, Any]) -> None:
    run_id = await _running_run(project)
    assert (await client.get(f"/api/v1/runs/{run_id}/transcript")).status_code == 404

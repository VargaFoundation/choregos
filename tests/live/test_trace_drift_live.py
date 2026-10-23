"""The nightly ledger-vs-trace job against a real gateway, collector and Tempo (S40-20c).

What the unit tests cannot show: that the usage the job reads from Tempo's real answer, on the
spans the embedded gateway's callback really writes, is the usage the gateway answered with. The
stack is S40-20b's (`tests/live/otel/compose.yaml`, a mocked model, no provider key):

    docker compose -p s4020b -f tests/live/otel/compose.yaml up -d --wait
    CHOREGOS_LIVE_OTEL=1 uv run pytest tests/live/test_trace_drift_live.py -m live
    docker compose -p s4020b -f tests/live/otel/compose.yaml down -v
"""

from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from .conftest import require
from .test_otel_collector_live import GATEWAY, TEMPO, _chat, _mint_run_key

pytestmark = pytest.mark.live


async def _ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runs: dict[str, tuple[int, int]]) -> None:
    """A throwaway ledger: one collected run per entry, with its model line."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.models import CostLedger, Organization, Project, Run, WorkItem
    from choregos_core import utcnow

    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/ledger.db")
    reset_settings_cache()
    await db_session.dispose_engine()
    await db_session.create_all()
    ended = utcnow() - timedelta(minutes=5)
    async with db_session.session_scope(orgs="*") as session:
        org = Organization(slug="live", name="Live")
        session.add(org)
        await session.flush()
        project = Project(org_id=org.id, slug="proj", name="Proj")
        session.add(project)
        await session.flush()
        for run_id, (tokens_in, tokens_out) in runs.items():
            item = WorkItem(project_id=project.id, tracker_key=f"PROJ-{run_id[-8:]}", title="T", state="done")
            session.add(item)
            await session.flush()
            session.add(
                Run(
                    id=run_id,
                    work_item_id=item.id,
                    project_id=project.id,
                    stage_role="dev",
                    attempt=1,
                    status="succeeded",
                    ended_at=ended,
                    spend_collected=True,
                )
            )
            # No relationship orders the two inserts: the run goes first, for the ledger's key.
            await session.flush()
            session.add(
                CostLedger(
                    project_id=project.id,
                    work_item_id=item.id,
                    run_id=run_id,
                    kind="model",
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    ts=ended,
                )
            )


def _usage_of_calls(client: httpx.Client, run_id: str, calls: int) -> tuple[int, int]:
    """The gateway's own answer for each call: what the ledger would book."""
    key = _mint_run_key(client, run_id)
    tokens_in = tokens_out = 0
    for _ in range(calls):
        answered = _chat(client, key, "platform/standard")
        assert answered.status_code == 200, answered.text[:300]
        usage: dict[str, Any] = answered.json()["usage"]
        tokens_in += int(usage["prompt_tokens"])
        tokens_out += int(usage["completion_tokens"])
    return tokens_in, tokens_out


async def test_the_job_reads_the_gateway_s_usage_from_a_real_tempo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require("CHOREGOS_LIVE_OTEL")
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from choregos_api.trace_drift import TempoReader, compare
    from choregos_core import utcnow
    from choregos_core.telemetry import run_trace_ids
    from sqlalchemy import select

    agreeing, drifting = (f"PROJ-1-dev-{uuid4().hex[:8]}" for _ in range(2))
    with httpx.Client(timeout=60) as client:
        assert client.get(f"{GATEWAY}/health/liveliness").status_code == 200
        booked = _usage_of_calls(client, agreeing, calls=2)
        drifted = _usage_of_calls(client, drifting, calls=1)
        assert booked[0] > 0 and booked[1] > 0
        # Both traces in Tempo, with their gateway spans, before the job reads them.
        deadline = time.monotonic() + 90
        for run_id, expected in ((agreeing, 2), (drifting, 1)):
            trace_id = f"{run_trace_ids(run_id)[0]:032x}"
            while True:
                answer = client.get(f"{TEMPO}/api/traces/{trace_id}")
                if answer.status_code == 200 and answer.text.count('"gen_ai.usage.input_tokens"') >= expected:
                    break
                if time.monotonic() > deadline:
                    pytest.fail(f"the trace of {run_id} did not reach Tempo")
                time.sleep(2)

    # The ledger books what the gateway answered for one run, and twice that for the other.
    await _ledger(tmp_path, monkeypatch, {agreeing: booked, drifting: (drifted[0] * 2, drifted[1] * 2)})
    reader = TempoReader(TEMPO)
    try:
        now = utcnow()
        report = await compare(reader, since=now - timedelta(hours=1), until=now, tolerance=0.0)
    finally:
        await reader.aclose()
    assert (report.runs, report.matched, report.drift, report.missing, report.errors) == (2, 1, 1, 0, [])
    async with session_scope(orgs="*") as session:
        (line,) = (await session.execute(select(AuditLog).where(AuditLog.action == "trace.drift"))).scalars()
    assert line.payload["run_id"] == drifting
    assert line.payload["trace"] == {"tokens_in": drifted[0], "tokens_out": drifted[1], "spans": 1}
    assert line.payload["ledger"] == {"tokens_in": drifted[0] * 2, "tokens_out": drifted[1] * 2}

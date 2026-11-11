"""A run whose work item already holds an earlier attempt's material, and the ways a run ends (S65-01)."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from typing import Any

_KEYS = itertools.count(1)

#: What the work item holds before anything is posted late: the next attempt's material.
PR_URL = "https://github.com/varga/billing-api/pull/41"
ALLOWED_PATHS = ["src/orders/**", "tests/orders/**"]
DOCUMENTS = {"spec_markdown": "# The spec the next attempt reads"}

#: A result a process posts after its run ended: it would move every field above.
LATE_RESULT: dict[str, Any] = {
    "schema": "choregos/StageResult/v1",
    "status": "done",
    "summary": "posted after the run ended",
    "outputs": {
        "size": "XL",
        "risk": "high",
        "allowed_paths": ["**"],
        "spec_markdown": "# A spec rewritten by a run nobody waits for",
    },
    "artifacts": {"pr_url": "https://github.com/varga/billing-api/pull/999"},
}


def _stage_input(run_id: str) -> dict[str, Any]:
    import choregos_contracts

    example = choregos_contracts.schemas_dir() / "examples" / "stage-input.example.json"
    return {**json.loads(example.read_text(encoding="utf-8")), "run_id": run_id}


async def a_run(project: dict[str, Any], *, status: str = "running", **columns: Any) -> str:
    """A run of a work item that already has a pull request, a size, a risk, a scope and a spec."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    number = next(_KEYS)
    run_id = f"varga-billing-api-{number}-t-implement-1"
    async with session_scope(orgs="*") as session:
        item = WorkItem(
            project_id=project["id"],
            tracker_key=f"varga/billing-api#{number}",
            title="Credit notes are not deducted from the total",
            state="in_progress",
            size="M",
            risk="low",
            pr_url=PR_URL,
            allowed_paths=list(ALLOWED_PATHS),
            documents=dict(DOCUMENTS),
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=run_id,
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="implement",
                transition_id="t-implement",
                attempt=1,
                status=status,
                stage_input=_stage_input(run_id),
                allowed_paths=list(ALLOWED_PATHS),
                **columns,
            )
        )
    return run_id


def token_of(run_id: str) -> dict[str, str]:
    from choregos_api.security import mint_run_token

    token = mint_run_token(
        run_id, project_slug="billing-api", work_item_key="varga/billing-api#1", ttl_minutes=30
    )
    return {"Authorization": f"Bearer {token}"}


async def close(run_id: str, *, status: str = "failed", reason: str = "timeout") -> None:
    """Close the row as the orchestrator does when it stops waiting (`close_run`, ADR 0143)."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_contracts import StageResult
    from choregos_core.run_closure import close_run

    async with session_scope(orgs="*") as session:
        run = await session.get(Run, run_id)
        assert run is not None
        assert close_run(run, StageResult.failure(f"the wait ended ({reason})", reason), status=status)


@dataclass(frozen=True)
class Kept:
    """Everything a late result could move: the run, its work item, what the platform recorded."""

    status: str
    result: dict[str, Any] | None
    ended_at: Any
    pr_url: str | None
    size: str | None
    risk: str | None
    allowed_paths: list[str]
    run_allowed_paths: list[str]
    documents: dict[str, Any]
    finished_events: int
    artifacts: int
    receipts: int
    findings: int
    human_requests: int
    ledger_lines: int


async def kept(run_id: str) -> Kept:
    from choregos_api.db.models import (
        ArtifactRecord,
        CostLedger,
        Event,
        Finding,
        HandOffReceipt,
        HumanRequest,
        Run,
        WorkItem,
    )
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from sqlalchemy import func, select

    async with session_scope(orgs="*") as session:
        run = await session.get(Run, run_id)
        assert run is not None
        item = await session.get(WorkItem, run.work_item_id)
        assert item is not None

        async def count(model: Any, *where: Any) -> int:
            return int(
                (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()
            )

        return Kept(
            status=run.status,
            result=run.result,
            ended_at=run.ended_at,
            pr_url=item.pr_url,
            size=item.size,
            risk=item.risk,
            allowed_paths=list(item.allowed_paths or []),
            run_allowed_paths=list(run.allowed_paths or []),
            documents=dict(item.documents or {}),
            finished_events=await count(
                Event, Event.type == str(EventType.RUN_FINISHED), Event.subject == run_id
            ),
            artifacts=await count(ArtifactRecord, ArtifactRecord.work_item_id == item.id),
            receipts=await count(HandOffReceipt, HandOffReceipt.run_id == run_id),
            findings=await count(Finding, Finding.origin_run_id == run_id),
            human_requests=await count(HumanRequest, HumanRequest.work_item_id == item.id),
            ledger_lines=await count(CostLedger, CostLedger.run_id == run_id),
        )

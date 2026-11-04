"""What the prompt of a run embeds from earlier steps leaves a "read" receipt (S25-04).

The next step reads the spec first in its prompt, rendered when the run is created: that is where
WHICH revision it received has to be said. A replayed activity does not double the receipt (ADR 0008).
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

SPEC = "# Spec\n\nDeduct credit notes from the total, rounded half to even.\n"


async def _prepare(setup: Any) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activities

    return await activities.prepare_stage(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-implement",
            "role": "implement",
            "actor": "dev",
            "from_state": "ready",
            "to_state": "in_progress",
            "attempt": 1,
        }
    )


async def _receipts(run_id: str) -> list[tuple[str, str, str]]:
    from choregos_api.db.models import HandOffReceipt
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        rows = (
            await session.execute(select(HandOffReceipt).where(HandOffReceipt.run_id == run_id))
        ).scalars()
        return [(r.output, r.kind, r.digest) for r in rows]


async def test_the_implementation_prompt_reads_the_spec_revision(setup: Any) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.recus import empreinte

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.documents = {"spec_markdown": SPEC}

    plan = await _prepare(setup)
    run_id = str(plan["run_id"])
    assert SPEC in plan["stage_input"]["playbook"]["prompt"]
    assert await _receipts(run_id) == [("spec_markdown", "read", empreinte(SPEC))]

    # The activity replayed on the same run does not make a second receipt.
    await _prepare(setup)
    assert await _receipts(run_id) == [("spec_markdown", "read", empreinte(SPEC))]


async def test_without_a_spec_the_prompt_reads_nothing(setup: Any) -> None:
    plan = await _prepare(setup)
    assert await _receipts(str(plan["run_id"])) == []

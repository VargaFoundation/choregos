"""`load_context` reads the state and the attempts as one moment of the database (S22-35e, #748).

`mirror_state` commits the state and the runs' `decided_at` together. A restart that read the
state, then lost a commit before reading the attempts, got the OLD state with the NEW attempts and
played `t-refine` again as attempt 2. The commit is forced between the two reads, with no sleep.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

BEFORE = "refining"
AFTER = "awaiting_spec_approval"


async def _a_succeeded_run_not_decided_yet(setup: Fixture) -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.state = BEFORE
        session.add(
            Run(
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                transition_id="t-refine",
                stage_role="refine",
                attempt=1,
                status="succeeded",
            )
        )


async def _commit_the_mirror(setup: Fixture) -> None:
    """What `mirror_state` commits, in one transaction: the new state, and the decision applied."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from sqlalchemy import update

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.state = AFTER
        await session.execute(
            update(Run).where(Run.work_item_id == setup.work_item_id).values(decided_at=utcnow())
        )


async def test_a_commit_between_the_two_reads_does_not_replay_the_stage(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_orchestrator.activities import interpretation

    await _a_succeeded_run_not_decided_yet(setup)
    real = interpretation.attempts_spent
    calls = 0

    async def commit_before_the_first_read(session: Any, work_item_id: str) -> dict[str, int]:
        nonlocal calls
        calls += 1
        if calls == 1:
            await _commit_the_mirror(setup)
        return await real(session, work_item_id)

    monkeypatch.setattr(interpretation, "attempts_spent", commit_before_the_first_read)

    context = await interpretation.load_context(
        {"project_id": setup.project_id, "work_item_id": setup.work_item_id}
    )

    # Either moment is fine; the mix of the two is the bug: the old state with attempt 1 spent
    # replays `t-refine` as attempt 2, the new state with none spent hands the old run back later.
    pair = (context["state"], context["attempts_spent"])
    assert pair in [(BEFORE, {}), (AFTER, {"t-refine": 1})], pair
    assert calls >= 2, "the reads were not repeated"

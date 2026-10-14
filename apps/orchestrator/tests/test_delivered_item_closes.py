"""A delivered work item closes its tracker issue, naming its release (S22-28).

Nothing anywhere closed an issue: `close_out` mirrored the terminal state and stopped there. On the
dev tenant, `DiametralGroup/choregos-sandbox-dev#4` and `#6` went to production with the train
(`R-2026.10.10-1`, `R-2026.10.10-2`) and their GitHub issues stayed open.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .conftest import Fixture
from .test_train_et_ticket import _jusqu_a, _jusqu_au_train, _politique_de_prod, _train

pytestmark = pytest.mark.asyncio

MARKER = "<!-- choregos:delivered -->"


async def _deliver(setup: Fixture, temporal_env: Any, worker_factory: Any) -> dict[str, Any]:
    await _politique_de_prod(setup, approbation=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, boarding = await _jusqu_au_train(setup, temporal_env)
            train = await _train(temporal_env, setup)
            await train.signal("merged", boarding)
            await train.signal("depart_now", {"by": "augustin"})
            await _jusqu_a(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("approve", {"by": "marie"})
            result: dict[str, Any] = await asyncio.wait_for(ticket.result(), timeout=60)
            await train.signal("abort", {"by": "test"})
            await train.result()
    return result


async def test_a_work_item_delivered_by_the_train_closes_its_issue_naming_the_release(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    result = await _deliver(setup, temporal_env, worker_factory)
    assert result["state"] == "deployed_prod"

    tracker = setup.adapters.tracker
    item = tracker.items[setup.tracker_key]
    assert item.closed_at is not None, "the delivered work item's issue stays open"
    comment = tracker.status_comment(setup.tracker_key, MARKER)
    assert comment is not None
    assert "release `R-" in comment and "`prod`" in comment, comment

    # A replayed close_out (activity retry) posts no second comment and does not reopen anything.
    from choregos_orchestrator.activities.tracker import close_out

    closed_at = item.closed_at
    again = await close_out({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
    assert again["tracker_closed"] is True
    assert [c.marker for c in item.comments].count(MARKER) == 1
    assert item.closed_at == closed_at


async def test_a_tracker_failure_does_not_kill_the_delivered_work_item(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.tracker.failure = "GitHub 502"
    result = await _deliver(setup, temporal_env, worker_factory)
    assert result["state"] == "deployed_prod"
    assert setup.adapters.tracker.items[setup.tracker_key].closed_at is None


async def test_an_abandoned_or_never_released_work_item_is_not_closed(setup: Fixture) -> None:
    """Abandoned after a departure, or terminal without a train: neither is a delivery."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.tracker import close_out

    for state, documents in (("abandoned", {"deployed_release": "r-1"}), ("deployed_prod", {})):
        async with session_scope() as session:
            row = await session.get(WorkItem, setup.work_item_id)
            assert row is not None
            row.state, row.documents, row.closed_at = state, documents, None
        result = await close_out({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
        assert result["tracker_closed"] is False, state
    assert ("close_item", setup.tracker_key) not in setup.adapters.tracker.calls

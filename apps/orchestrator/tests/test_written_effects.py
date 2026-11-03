"""What the platform does is written down (#175): `does: open_pr` opens the PR, `does: merge_pr` merges it,
whatever the name of the target state. Before, it was the `pr_` and `merged` prefixes of the name:
renaming the state from the console removed the effect without saying so. Here `pr_open` becomes
`review` and `merged` becomes `merged_ok` through the typed edit; the PR still opens and merges.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture, stage_result
from .test_interpreter import _wait_state, scripted, start

pytestmark = pytest.mark.asyncio


async def _publish(setup: Fixture, text: str) -> None:
    """The project's active workflow becomes `text` — what a publication would do."""
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope
    from choregos_core import checksum, parse_workflow
    from sqlalchemy import select

    wf, report = parse_workflow(text, strict=False)
    assert report.valid, [e.message for e in report.errors]
    async with session_scope() as session:
        definition = (
            await session.execute(
                select(WorkflowDef).where(WorkflowDef.project_id == setup.project_id, WorkflowDef.is_active)
            )
        ).scalar_one()
        definition.yaml = text
        definition.json_doc = wf.model_dump(mode="json", by_alias=True, exclude_none=True)
        definition.checksum = checksum(wf)


async def test_renamed_states_still_open_and_merge_the_pr(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_core.dsl import template_yaml
    from choregos_core.dsl.edition import edit

    from .test_interpreter import _pr_ref

    renamed = edit(
        template_yaml("default-simple"),
        [
            {"op": "rename_state", "from": "pr_open", "to": "review"},
            {"op": "rename_state", "from": "merged", "to": "merged_ok"},
        ],
    )
    assert "pr_open" not in renamed.yaml and "merged:" not in renamed.yaml
    await _publish(setup, renamed.yaml)
    scripted(
        setup.adapters,
        stage_result("spec written", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
        stage_result(
            "implemented", artifacts={"branch": "choregos/123", "commits": ["fix(orders): credits"]}
        ),
        stage_result("verified"),
    )
    setup.adapters.cd.set_health("billing-api", "Healthy")
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal(
            "human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"}
        )
        await _wait_state(handle, "review")
        assert setup.adapters.scm.prs, "`does: open_pr` opened the PR — the state is no longer called `pr_…`"
        assert not setup.adapters.scm.merge_queue

        setup.adapters.scm.set_checks(_pr_ref(setup), "success")
        setup.adapters.scm.submit_review(_pr_ref(setup), "marie", "approved")
        for number, event in enumerate(("scm.check.completed", "scm.pr.review_submitted")):
            payload = {"conclusion": "success"} if number == 0 else {"state": "approved"}
            await handle.signal(
                "inbound",
                {"type": event, "source": "github", "delivery_id": f"d{number}", "payload": payload},
            )
        await _wait_state(handle, "merged_ok")
        pr = _pr_ref(setup)
        assert [(r.repo, r.number) for r in setup.adapters.scm.merge_queue] == [(pr.repo, pr.number)], (
            "`does: merge_pr` put the PR in the merge queue — the state is no longer called `merged…`"
        )
        await handle.terminate("end of the test")

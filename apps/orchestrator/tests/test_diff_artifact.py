# SPDX-License-Identifier: Apache-2.0
"""The platform records a branch's diff, and nothing else may (ADR 0129 §7, §8)."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import artifact_digest

from .conftest import Fixture, stage_result

HUNK = "@@ -1 +1 @@\n-old\n+new"


async def _diff_rows(item_id: str) -> list[Any]:
    from choregos_api.db.models import ArtifactRecord
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(
            select(ArtifactRecord)
            .where(ArtifactRecord.work_item_id == item_id, ArtifactRecord.name == "diff")
            .order_by(ArtifactRecord.version)
        )
        return list(rows.scalars().all())


async def _collect(setup: Fixture, run_id: str, files: list[tuple[Any, ...]]) -> dict[str, Any]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.scm import collect_run_artifacts

    setup.adapters.scm.set_diff("varga/billing-api", "main", "choregos/123", files)
    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                status="succeeded",
                result=stage_result().model_dump(mode="json", by_alias=True),
            )
        )
    return await collect_run_artifacts(
        {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "run_id": run_id}
    )


async def test_the_patch_becomes_an_artifact_of_the_run_that_left_it(setup: Fixture) -> None:
    await _collect(setup, "run-1", [("a.py", 1, 1, HUNK)])
    [row] = await _diff_rows(setup.work_item_id)
    assert (row.type, row.kind, row.media_type, row.version) == ("diff", "diff", "text/x-diff", 1)
    assert row.run_id == "run-1"
    assert row.content == f"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n{HUNK}\n"
    assert row.digest == artifact_digest(row.content.encode("utf-8"))
    assert row.display["diff"] == {
        "files": 1,
        "additions": 1,
        "deletions": 1,
        "truncated": False,
        "withheld": [],
    }


async def test_the_patch_stays_out_of_the_run_result(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    await _collect(setup, "run-1", [("a.py", 1, 1, HUNK)])
    async with session_scope() as session:
        run = await session.get(Run, "run-1")
        assert run is not None
        assert "-old" not in run.result["artifacts"]["reports"]["diff_files"]


async def test_a_branch_that_did_not_change_makes_no_new_revision(setup: Fixture) -> None:
    await _collect(setup, "run-1", [("a.py", 1, 1, HUNK)])
    await _collect(setup, "run-2", [("a.py", 1, 1, HUNK)])
    assert len(await _diff_rows(setup.work_item_id)) == 1
    await _collect(setup, "run-3", [("a.py", 2, 1, HUNK + "\n+more")])
    assert [row.run_id for row in await _diff_rows(setup.work_item_id)] == ["run-1", "run-3"]


async def test_a_step_that_changed_nothing_records_nothing(setup: Fixture) -> None:
    assert await _collect(setup, "run-1", []) == {"files": 0}
    assert await _diff_rows(setup.work_item_id) == []


async def test_a_secret_in_the_patch_never_reaches_the_row(setup: Fixture) -> None:
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    await _collect(setup, "run-1", [("a.py", 1, 0, f"@@ -0,0 +1 @@\n+{key}")])
    [row] = await _diff_rows(setup.work_item_id)
    assert key not in row.content
    assert row.display["diff"]["withheld"] == ["AWS key"]
    assert "a.py" in row.content


async def test_a_failure_to_record_does_not_cost_the_activity(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_orchestrator.activities import outcome

    async def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("store down")

    monkeypatch.setattr(outcome, "record_diff", boom)
    assert await _collect(setup, "run-1", [("a.py", 1, 1, HUNK)]) == {"files": 1}


async def test_an_agent_cannot_write_the_diff(setup: Fixture) -> None:
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import record_inline_outputs

    async with session_scope() as session:
        run = Run(
            id="run-liar",
            work_item_id=setup.work_item_id,
            project_id=setup.project_id,
            stage_role="implement",
            status="running",
        )
        session.add(run)
        await session.flush()
        item = await session.get(WorkItem, setup.work_item_id)
        project = await session.get(Project, setup.project_id)
        assert item is not None and project is not None
        recorded = await record_inline_outputs(
            session, org_id=project.org_id, item=item, run=run, outputs={"diff": "nothing to see"}
        )
    assert recorded == []
    assert await _diff_rows(setup.work_item_id) == []

"""`adr_number_free` reads the default branch THROUGH THE REPOSITORY, at the time of judging (#287, S22-20).

Two studies running together number their ADR "the largest existing plus one": the second one to
merge would carry a duplicate. The activity lists the ADRs already on `main` and the guard refuses
the taken number.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

REPOSITORY = "varga/billing-api"
RULE = {"name": "adr_number_free"}


async def test_a_number_taken_on_the_default_branch_is_refused(setup: Any) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    payload = {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "gates": [RULE]}
    scm = setup.adapters.scm
    scm.set_diff(REPOSITORY, "main", "choregos/123", [("docs/adr/0042-rls.md", 16, 0)])
    [free] = await evaluate_gates(payload)
    assert free["passed"] is True, free["detail"]

    # Another study merged its 0042 in the meantime.
    await scm.commit_files(REPOSITORY, "main", {"docs/adr/0042-cache.md": "# Cache\n"}, "other study")
    [taken] = await evaluate_gates(payload)
    assert taken["passed"] is False
    assert taken["annotations"] == ["docs/adr/0042-rls.md"]
    assert "already taken on the default branch by docs/adr/0042-cache.md" in taken["detail"]


async def test_without_the_repository_listing_the_guard_refuses(setup: Any, monkeypatch: Any) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    async def down(*_: Any) -> list[str]:
        raise RuntimeError("GitHub unreachable")

    monkeypatch.setattr(setup.adapters.scm, "list_files", down)
    setup.adapters.scm.set_diff(REPOSITORY, "main", "choregos/123", [("docs/adr/0042-rls.md", 16, 0)])
    payload = {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "gates": [RULE]}
    [blind] = await evaluate_gates(payload)
    assert blind["passed"] is False and "default branch unavailable" in blind["detail"]

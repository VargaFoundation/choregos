"""`docs_updated` reads the branch diff the activity fetches for it (S39-15)."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

RULE = {"name": "docs_updated", "params": {}}
PATCH = "@@ -0,0 +1 @@\n+Invoices are queued."


async def test_the_diff_reaches_the_documentation_gate(setup: Any) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    payload = {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "gates": [RULE]}
    setup.adapters.scm.set_diff(
        "varga/billing-api",
        "main",
        "choregos/123",
        [("src/x.py", 10, 0), ("docs/x.md", 5, 0, PATCH)],
    )
    [ok] = await evaluate_gates(payload)
    assert ok["passed"] is True, ok["detail"]
    assert "diff unavailable" not in ok["detail"]

    setup.adapters.scm.set_diff("varga/billing-api", "main", "choregos/123", [("src/x.py", 10, 0)])
    [refused] = await evaluate_gates(payload)
    assert refused["passed"] is False
    assert "diff unavailable" not in refused["detail"]
    assert "src/x.py" in refused["annotations"]

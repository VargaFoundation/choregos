"""`sandbox.llm_security_analyzer` marks the run's gateway key, which the gateway's guard reads (S40-21).

The flag was declared and enforced nowhere. It now means: the key is minted with
`choregos_llm_guard: redact` in its metadata, and LiteLLM masks secrets for that key before the
provider call (ADR 0084). The metadata is the platform's: the agent holds the key, not its record.
"""

from __future__ import annotations

import json

import pytest
from choregos_core.llm_guardrail import GUARD_METADATA_KEY, GUARD_REDACT

from .conftest import Fixture
from .test_budget_reservation import _plan

pytestmark = pytest.mark.asyncio


async def _analyzer(setup: Fixture, on: bool) -> None:
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(PolicyDef).where(PolicyDef.project_id == setup.project_id, PolicyDef.is_active)
            )
        ).scalar_one()
        doc = json.loads(json.dumps(row.json_doc))
        doc.setdefault("sandbox", {})["llm_security_analyzer"] = on
        row.json_doc = doc


async def _start(setup: Fixture) -> dict[str, object]:
    from choregos_orchestrator.activities.stage import prepare_stage, start_run

    prepared = await prepare_stage(_plan(setup))
    await start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    [key] = setup.adapters.gateway.keys.values()
    return dict(key.metadata)


async def test_the_policy_marks_the_run_key_for_the_gateway_guard(setup: Fixture) -> None:
    await _analyzer(setup, True)
    metadata = await _start(setup)
    assert metadata[GUARD_METADATA_KEY] == GUARD_REDACT
    assert metadata["run_id"], "the run's own metadata stays"


async def test_without_the_policy_the_key_is_not_marked(setup: Fixture) -> None:
    await _analyzer(setup, False)
    assert GUARD_METADATA_KEY not in await _start(setup)

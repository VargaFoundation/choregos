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


async def test_a_gateway_without_the_guard_refuses_the_stage_before_anything_is_reserved(
    setup: Fixture,
) -> None:
    """S40-21b: on `direct` or `openai-compatible` the mark would reach no guard. The stage is
    refused as a configuration the project cannot run, which the interpreter escalates."""
    from choregos_orchestrator.activities.stage import prepare_stage
    from temporalio.exceptions import ApplicationError

    await _analyzer(setup, True)
    setup.adapters.gateway.capabilities = frozenset({"virtual_keys", "hard_budget"})
    with pytest.raises(ApplicationError) as refused:
        await prepare_stage(_plan(setup))
    assert refused.value.type == "ModelResolutionError" and refused.value.non_retryable
    assert "llm_guard" in str(refused.value)
    assert not setup.adapters.gateway.keys


async def test_without_the_policy_a_gateway_without_the_guard_still_runs(setup: Fixture) -> None:
    await _analyzer(setup, False)
    setup.adapters.gateway.capabilities = frozenset({"virtual_keys", "hard_budget"})
    assert GUARD_METADATA_KEY not in await _start(setup)


async def test_a_key_is_never_minted_unguarded_on_a_resume_either() -> None:
    """`mint_key` is every run key's door, the resumed run's included: it refuses on its own."""
    from types import SimpleNamespace

    from choregos_adapters.errors import ConfigurationError
    from choregos_adapters.fakes.gateway import FakeGateway
    from choregos_orchestrator.activities.run_key import mint_key

    gateway = FakeGateway(capabilities=frozenset({"virtual_keys"}))
    bundle = SimpleNamespace(
        policy=SimpleNamespace(sandbox=SimpleNamespace(llm_security_analyzer=True)),
        adapters=SimpleNamespace(gateway=gateway),
    )
    with pytest.raises(ConfigurationError, match="llm_guard"):
        await mint_key(bundle, {"run_id": "r1"}, budget_usd=1.0, ttl_s=60, models=[])
    assert not gateway.keys

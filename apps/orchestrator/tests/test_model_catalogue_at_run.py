"""The organisation's model catalogue at run time (S39-07b, ADR 0060).

A model disabled AFTER an agent was published stops the stage in `prepare_stage` with a refusal
that is not retried (the interpreter escalates the work item), before any key is minted. A price
override changes what the ledger books; a gateway that cannot read a spend back leaves the line
unmeasured.
"""

from __future__ import annotations

from typing import Any

import pytest
from tranibor_core import Spend

from .conftest import Fixture
from .test_resolver_sees_the_gateway import _prepare


async def _catalogue(setup: Fixture, **row: Any) -> None:
    from tranibor_api.db.models import ModelCatalogueRow, Project
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        session.add(ModelCatalogueRow(org_id=project.org_id, source="gateway", notes="", **row))


async def _curate(setup: Fixture) -> None:
    from tranibor_api.db.models import Organization, Project
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        organisation = await session.get(Organization, project.org_id)
        assert organisation is not None
        organisation.settings = {"models": {"mode": "curated", "tiers": {}}}


async def test_a_model_disabled_after_publication_stops_the_stage_without_a_key(setup: Fixture) -> None:
    from temporalio.exceptions import ApplicationError

    await _catalogue(
        setup,
        model_name="platform/standard",
        display_name="standard",
        enabled=False,
        allowed_roles=[],
        usages=["agent"],
        updated_by="ada@northwind.dev",
    )
    with pytest.raises(ApplicationError) as refused:
        await _prepare(setup)
    assert refused.value.non_retryable
    assert refused.value.type == "ModelResolutionError"
    assert "disabled by ada@northwind.dev" in str(refused.value)
    assert setup.adapters.gateway.keys == {}, "no key was minted"


async def test_curated_mode_refuses_a_model_nobody_enabled(setup: Fixture) -> None:
    from temporalio.exceptions import ApplicationError

    await _curate(setup)
    with pytest.raises(ApplicationError, match="not in the organisation's model catalogue"):
        await _prepare(setup)


async def test_an_enabled_model_still_runs_in_curated_mode(setup: Fixture) -> None:
    await _curate(setup)
    await _catalogue(
        setup, model_name="platform/standard", display_name="s", enabled=True, allowed_roles=["implement"],
        usages=["agent"],
    )  # fmt: skip
    prepared = await _prepare(setup)
    assert prepared["stage_input"]["model"]["litellm_model"] == "platform/standard"


async def test_a_role_the_model_is_not_allowed_for_is_refused(setup: Fixture) -> None:
    from temporalio.exceptions import ApplicationError

    await _catalogue(
        setup, model_name="platform/standard", display_name="s", enabled=True, allowed_roles=["review"],
        usages=["agent"],
    )  # fmt: skip
    with pytest.raises(ApplicationError, match="not allowed for the role `implement`"):
        await _prepare(setup)


# ───────────────────────────── the ledger ─────────────────────────────


async def _collect(
    setup: Fixture, *, capabilities: frozenset[str] | None = None, spend: Spend | None = None
) -> dict[str, Any]:
    from sqlalchemy import select
    from tranibor_api.db.models import CostLedger, Run
    from tranibor_api.db.session import session_scope
    from tranibor_core import utcnow
    from tranibor_orchestrator.activities.outcome import collect_spend

    gateway = setup.adapters.gateway
    if capabilities is not None:
        gateway.capabilities = capabilities
    key = await gateway.mint_key({}, 5.0, 600, ["platform/standard"])
    gateway.spends[key.key_id] = spend or Spend(
        tokens_in=100_000, tokens_out=10_000, cost_usd=0.5, requests=3, models_used=["platform/standard"]
    )
    async with session_scope() as session:
        session.add(
            Run(
                id="run-price",
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                model="platform/standard",
                status="running",
                gateway_key_id=key.key_id,
                started_at=utcnow(),
            )
        )
    result = await collect_spend({"project_id": setup.project_id, "run_id": "run-price"})
    async with session_scope() as session:
        line = (
            await session.execute(select(CostLedger).where(CostLedger.run_id == "run-price"))
        ).scalar_one()
    return {"result": result, "cost": line.cost_usd, "line": line}


async def test_a_price_override_replaces_the_gateway_figure_in_the_ledger(setup: Fixture) -> None:
    await _catalogue(
        setup, model_name="platform/standard", display_name="s", enabled=True, allowed_roles=[],
        usages=["agent"], input_per_1k_override=0.002, output_per_1k_override=0.01,
    )  # fmt: skip
    booked = await _collect(setup)
    assert booked["cost"] == pytest.approx(100 * 0.002 + 10 * 0.01)  # 0.30, not the gateway's 0.50
    assert booked["result"]["priced_by"] == "override"
    assert booked["result"]["gateway_cost_usd"] == 0.5


async def test_without_an_override_the_gateway_figure_stands(setup: Fixture) -> None:
    booked = await _collect(setup)
    assert booked["cost"] == pytest.approx(0.5)
    assert booked["result"]["priced_by"] == "gateway"


async def test_a_gateway_without_spend_readback_leaves_the_line_unmeasured(setup: Fixture) -> None:
    await _catalogue(
        setup, model_name="platform/standard", display_name="s", enabled=True, allowed_roles=[],
        usages=["agent"], input_per_1k_override=0.002, output_per_1k_override=0.01,
    )  # fmt: skip
    booked = await _collect(setup, capabilities=frozenset({"list_models"}))
    assert booked["result"]["unmeasured"] is True
    assert booked["result"]["priced_by"] == "none"
    assert booked["cost"] == pytest.approx(0.5), "an override changes nothing without a measured spend"


#: 100k input tokens: 40k fresh, 50k read from the cache, 10k written (4k with the 1-hour TTL).
CACHE_HEAVY = Spend(
    tokens_in=100_000, tokens_out=10_000, tokens_cached=50_000, tokens_cache_write=10_000,
    tokens_cache_write_1h=4_000, cost_usd=0.5, requests=3, models_used=["platform/standard"],
)  # fmt: skip


async def test_an_override_prices_cache_reads_and_writes_at_their_own_rates(setup: Fixture) -> None:
    """S4-12 (#1893, #1895): with an override, a cache read costs 0.1x, a 5-minute write 1.25x and a
    1-hour write 2x the input price on an Anthropic model, not 1x each."""
    await _catalogue(
        setup, model_name="platform/standard", display_name="s", enabled=True, allowed_roles=[],
        usages=["agent"], input_per_1k_override=0.002, output_per_1k_override=0.01,
        gateway_facts={"provider": "anthropic", "upstream_model": "anthropic/claude-sonnet-4"},
    )  # fmt: skip
    booked = await _collect(setup, spend=CACHE_HEAVY)
    fresh, read, write_5m, write_1h = 40 * 0.002, 50 * 0.002 * 0.1, 6 * 0.002 * 1.25, 4 * 0.002 * 2.0
    assert booked["cost"] == pytest.approx(fresh + read + write_5m + write_1h + 10 * 0.01)
    assert booked["cost"] < 100 * 0.002 + 10 * 0.01, "not every input token at one rate"
    assert booked["result"]["cache_multipliers"] == {
        "read": 0.1, "write_5m": 1.25, "write_1h": 2.0, "source": "family_default",
    }  # fmt: skip
    assert (booked["line"].tokens_cache_write, booked["line"].tokens_cache_write_1h) == (10_000, 4_000)


async def test_the_entrys_own_multipliers_win_over_the_family_default(setup: Fixture) -> None:
    await _catalogue(
        setup, model_name="platform/standard", display_name="s", enabled=True, allowed_roles=[],
        usages=["agent"], input_per_1k_override=0.002, output_per_1k_override=0.01,
        gateway_facts={"provider": "anthropic"},
        cache_read_multiplier=0.05, cache_write_1h_multiplier=3.0,
    )  # fmt: skip
    booked = await _collect(setup, spend=CACHE_HEAVY)
    expected = 40 * 0.002 + 50 * 0.002 * 0.05 + 6 * 0.002 * 1.25 + 4 * 0.002 * 3.0 + 10 * 0.01
    assert booked["cost"] == pytest.approx(expected)
    assert booked["result"]["cache_multipliers"]["source"] == "override"

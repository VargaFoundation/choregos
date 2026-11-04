"""The two "there is none" connectors: what they return, and what they admit."""

from __future__ import annotations

import pytest
from choregos_adapters.gateway.direct import DirectGateway
from choregos_adapters.tracker.internal import InternalTracker
from choregos_contracts import ProjectConfig
from choregos_core.domain import NewItem, TrackerStateMapping


async def test_the_direct_gateway_returns_a_unique_key_per_run() -> None:
    """`key_id` carries a unique index: a constant identifier made the second run fail."""
    gateway = DirectGateway("sk-perso", models=["claude"])
    a = await gateway.mint_key({"run_id": "r-1"}, budget_usd=5.0, ttl_s=60, models=["m"])
    b = await gateway.mint_key({"run_id": "r-2"}, budget_usd=5.0, ttl_s=60, models=["m"])
    assert a.key == b.key == "sk-perso"
    assert a.key_id == "direct:r-1" and b.key_id == "direct:r-2"
    sans_run = await gateway.mint_key({}, 1.0, 60, [])
    assert sans_run.key_id.startswith("direct:") and sans_run.key_id != "direct:"


async def test_the_direct_gateway_measures_nothing_and_says_so_with_a_zero() -> None:
    gateway = DirectGateway()
    spend = await gateway.spend("direct:r-1")
    assert spend.cost_usd == 0 and spend.tokens_in == 0
    assert await gateway.revoke("direct:r-1") is None
    assert [m.model_name for m in await DirectGateway(models=["a", "b"]).list_models()] == ["a", "b"]


async def test_the_internal_tracker_does_not_own_the_tickets() -> None:
    """`create_item` returns an EMPTY key: it is the platform that names, and returning the title
    produced unreadable and non-unique keys (bench of 2026-09-24)."""
    tracker = InternalTracker()
    assert tracker.owns_items is False
    assert await tracker.create_item(NewItem(title="Un titre", body="")) == ""
    item = await tracker.fetch_item("RH-1")
    assert item.key == "RH-1" and item.title == "RH-1"
    assert await tracker.list_candidates(ProjectConfig(slug="p", org="o")) == []


async def test_the_internal_tracker_accepts_no_webhook() -> None:
    tracker = InternalTracker()
    assert tracker.verify_webhook({"X-Anything": "x"}, b"{}") is False
    assert tracker.parse_webhook({}, b"{}") == []
    assert await tracker.set_state("RH-1", TrackerStateMapping(label="x")) is None
    assert await tracker.comment("RH-1", "texte") == ""


@pytest.mark.parametrize("environment", ("staging", "prod"))
def test_the_direct_gateway_refuses_to_be_built_in_production(
    environment: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accounting that is switched off must not be so by accident.

    `direct` returns the key it was given: no virtual key per run, so no hard ceiling
    and no measured cost. The bench ran like this for a week and "proved" a spend ceiling
    that had never measured any spend (BLOCKERS, S12). On a serious environment,
    it is a refusal, as `garde.yaml` refuses an embedded dependency.
    """
    from choregos_adapters import build
    from choregos_adapters.errors import ConfigurationError

    # `CHOREGOS_FAKES=1` (set by the API suite when everything runs together) short-circuits
    # the factory and returns a fake: without this zero, the test passed alone and turned red in a group.
    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    monkeypatch.setenv("CHOREGOS_ENV", environment)
    with pytest.raises(ConfigurationError, match="refused"):
        build("gateway", "direct", {})


def test_elle_se_construit_ailleurs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A development workstation or a bench keeps the right to bring its own key."""
    from choregos_adapters import build

    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    monkeypatch.setenv("CHOREGOS_ENV", "dev")
    assert isinstance(build("gateway", "direct", {"key": "sk-perso"}), DirectGateway)

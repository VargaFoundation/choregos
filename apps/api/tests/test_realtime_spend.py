"""What a voice session spends, priced by its relay (S41-15, ADR 0080 §10): the arithmetic alone."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_api.db.models import ModelCatalogueRow
from choregos_api.services.realtime_spend import (
    RealtimePrices,
    realtime_prices,
    relayed_spend,
    response_cost,
    session_cap,
)
from choregos_core import GatewayModel

PRICES = RealtimePrices(
    text_in=0.004, text_out=0.016, audio_in=0.032, audio_out=0.064, cached_in=0.0004, priced_by="gateway"
)
REALTIME = GatewayModel(
    model_name="openai/gpt-realtime",
    litellm_model="openai/gpt-realtime",
    input_cost_per_1k=0.004,
    output_cost_per_1k=0.016,
    input_audio_cost_per_1k=0.032,
    output_audio_cost_per_1k=0.064,
    cached_input_cost_per_1k=0.0004,
)


def _usage(**details: Any) -> dict[str, Any]:
    return {"input_tokens": 1000, "output_tokens": 400, **details}


def test_each_kind_of_token_is_priced_at_its_own_price() -> None:
    usage = _usage(
        input_token_details={
            "text_tokens": 200,
            "audio_tokens": 800,
            "cached_tokens": 300,
            "cached_tokens_details": {"text_tokens": 100, "audio_tokens": 200},
        },
        output_token_details={"text_tokens": 100, "audio_tokens": 300},
    )
    cost = response_cost(usage, PRICES)
    by_hand = (100 * 0.004 + 600 * 0.032 + 300 * 0.0004 + 100 * 0.016 + 300 * 0.064) / 1000
    assert cost.cost_usd == pytest.approx(by_hand)
    assert (cost.tokens_in, cost.tokens_out, cost.tokens_cached) == (1000, 400, 300)


def test_a_total_without_its_split_is_priced_at_the_dearer_kind() -> None:
    cost = response_cost(_usage(), PRICES)
    assert cost.cost_usd == pytest.approx((1000 * 0.032 + 400 * 0.064) / 1000)
    cheap_audio = RealtimePrices(0.05, 0.1, 0.01, 0.02, 0.005, "gateway")
    assert response_cost(_usage(), cheap_audio).cost_usd == pytest.approx((1000 * 0.05 + 400 * 0.1) / 1000)


def test_a_half_split_is_completed_from_the_total() -> None:
    usage = _usage(input_token_details={"text_tokens": 250}, output_token_details={"audio_tokens": 100})
    cost = response_cost(usage, PRICES)
    by_hand = (250 * 0.004 + 750 * 0.032 + 300 * 0.016 + 100 * 0.064) / 1000
    assert cost.cost_usd == pytest.approx(by_hand)


def test_cached_tokens_without_their_split_are_discounted_on_text_first() -> None:
    usage = _usage(
        input_token_details={"text_tokens": 200, "audio_tokens": 800, "cached_tokens": 300},
        output_token_details={"text_tokens": 400, "audio_tokens": 0},
    )
    cost = response_cost(usage, PRICES)
    by_hand = (0 * 0.004 + 700 * 0.032 + 300 * 0.0004 + 400 * 0.016) / 1000
    assert cost.cost_usd == pytest.approx(by_hand)


def test_a_usage_that_lies_is_bounded_by_its_totals() -> None:
    usage = {
        "input_tokens": 10,
        "output_tokens": -5,
        "input_token_details": {"text_tokens": 10, "audio_tokens": 0, "cached_tokens": 999},
    }
    cost = response_cost(usage, PRICES)
    assert cost.tokens_cached == 10 and cost.tokens_out == 0
    assert cost.cost_usd == pytest.approx(10 * 0.0004 / 1000)

    split = {
        "input_tokens": 10,
        "output_tokens": 0,
        "input_token_details": {
            "text_tokens": 10,
            "audio_tokens": 0,
            "cached_tokens": 60,
            "cached_tokens_details": {"text_tokens": 50, "audio_tokens": 10},
        },
    }
    cost = response_cost(split, PRICES)
    assert cost.tokens_cached == 10, "no more cached tokens of a kind than the kind has"
    assert cost.cost_usd == pytest.approx(10 * 0.0004 / 1000)


def test_the_gateways_prices_need_all_four_and_a_cached_one_defaults_to_the_fresh() -> None:
    assert realtime_prices(REALTIME, None) == PRICES
    no_cache = REALTIME.model_copy(update={"cached_input_cost_per_1k": None})
    assert realtime_prices(no_cache, None) == RealtimePrices(0.004, 0.016, 0.032, 0.064, 0.004, "gateway")
    for missing in (
        "input_cost_per_1k",
        "output_cost_per_1k",
        "input_audio_cost_per_1k",
        "output_audio_cost_per_1k",
    ):
        assert realtime_prices(REALTIME.model_copy(update={missing: None}), None) is None, missing


def test_an_organisation_override_prices_every_token() -> None:
    row = ModelCatalogueRow(
        model_name=REALTIME.model_name, input_per_1k_override=0.01, output_per_1k_override=0.02
    )
    unpriced = GatewayModel(model_name="openai/gpt-realtime", litellm_model="openai/gpt-realtime")
    assert realtime_prices(unpriced, row) == RealtimePrices(0.01, 0.02, 0.01, 0.02, 0.01, "override")
    half = ModelCatalogueRow(model_name=REALTIME.model_name, input_per_1k_override=0.01)
    assert realtime_prices(REALTIME, half) == PRICES, "half an override is none"


def test_the_cap_is_the_reservation_and_the_rate_for_each_minute_begun() -> None:
    assert session_cap(1.0, None, 3600, 60) == 1.0
    assert session_cap(1.0, 0.25, 0, 60) == 0.25, "the first minute is counted whole"
    assert session_cap(1.0, 0.25, 60, 60) == 0.25
    assert session_cap(1.0, 0.25, 61, 60) == 0.5
    assert session_cap(1.0, 0.25, 600, 60) == 1.0, "never above the reservation"


def test_the_relayed_spend_sums_what_the_relay_priced_only() -> None:
    payloads = [
        {"priced_by": "relay", "cost_usd": 0.25, "tokens_in": 10, "tokens_out": 4, "tokens_cached": 1},
        {"priced_by": "relay", "cost_usd": 0.5, "tokens_in": 20, "tokens_out": 8, "tokens_cached": 2},
        {"status": "completed", "usage": {"input_tokens": 999}},
    ]
    spend = relayed_spend(payloads, "openai/gpt-realtime")
    assert spend is not None
    assert (spend.cost_usd, spend.tokens_in, spend.tokens_out, spend.tokens_cached) == (0.75, 30, 12, 3)
    assert spend.requests == 2 and spend.models_used == ["openai/gpt-realtime"]
    assert relayed_spend([{"usage": {}}], "m") is None, "a prompt session books the gateway's"

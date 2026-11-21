# SPDX-License-Identifier: Apache-2.0
"""Cache tokens are priced at their own rates (S4-12, ADR 0200)."""

from __future__ import annotations

import pytest
from tranibor_core.cache_pricing import (
    ANTHROPIC,
    ANTHROPIC_HALF_READ,
    GEMINI,
    OPENAI,
    UNIT,
    CacheMultipliers,
    family_defaults,
    input_cost_usd,
    resolve,
)


@pytest.mark.parametrize(
    ("model", "provider", "expected"),
    [
        ("platform/standard", "anthropic", ANTHROPIC),
        ("claude-sonnet-4", "", ANTHROPIC),
        ("bedrock/anthropic.claude-opus-4", "bedrock", ANTHROPIC),
        ("claude-opus-5-5", "anthropic", ANTHROPIC_HALF_READ),
        ("anthropic/claude-sonnet-5.5", "", ANTHROPIC_HALF_READ),
        ("gpt-5", "openai", OPENAI),
        ("azure/gpt-4o", "azure", OPENAI),
        ("gemini/gemini-2.5-pro", "gemini", GEMINI),
        ("platform/local", "ollama", UNIT),
    ],
)
def test_the_default_comes_from_the_provider_family(
    model: str, provider: str, expected: CacheMultipliers
) -> None:
    assert family_defaults(model, provider) == expected


def test_each_kind_of_input_token_is_priced_at_its_own_rate() -> None:
    cost = input_cost_usd(
        price_per_1k=1.0,
        tokens_in=100_000,
        tokens_cached=50_000,
        tokens_cache_write=10_000,
        tokens_cache_write_1h=4_000,
        multipliers=ANTHROPIC,
    )
    assert cost == pytest.approx(40 + 5 + 6 * 1.25 + 4 * 2.0)


def test_a_one_hour_write_costs_more_than_a_five_minute_one() -> None:
    args = {"price_per_1k": 1.0, "tokens_in": 1_000, "tokens_cached": 0, "tokens_cache_write": 1_000}
    short = input_cost_usd(**args, tokens_cache_write_1h=0, multipliers=ANTHROPIC)
    long = input_cost_usd(**args, tokens_cache_write_1h=1_000, multipliers=ANTHROPIC)
    assert (short, long) == (pytest.approx(1.25), pytest.approx(2.0))
    assert long / short == pytest.approx(1.6)


def test_counts_above_their_whole_are_clamped_never_negative() -> None:
    cost = input_cost_usd(
        price_per_1k=1.0,
        tokens_in=1_000,
        tokens_cached=5_000,
        tokens_cache_write=5_000,
        tokens_cache_write_1h=9_000,
        multipliers=ANTHROPIC,
    )
    assert cost == pytest.approx(1_000 * 0.1 / 1000)


def test_the_gateways_cached_price_gives_the_read_multiplier_and_the_entry_overrides_it() -> None:
    gateway = resolve(
        model_name="gpt-5", provider="openai", input_cost_per_1k=0.01, cached_input_cost_per_1k=0.001
    )
    assert (gateway.multipliers.read, gateway.source) == (pytest.approx(0.1), "gateway")
    own = resolve(
        model_name="gpt-5", provider="openai", read=0.2, write_1h=3.0,
        input_cost_per_1k=0.01, cached_input_cost_per_1k=0.001,
    )  # fmt: skip
    assert (own.multipliers.read, own.multipliers.write_1h, own.source) == (0.2, 3.0, "override")
    assert own.multipliers.write_5m == OPENAI.write_5m, "a field left null keeps the family default"

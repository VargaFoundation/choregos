"""Hand-computed indicators, signals, and the causality of every family."""

from __future__ import annotations

import random

import pytest
from choregos_quant.strategies import BOUNDS, SIGNALS, Strategy, ema, rsi, signals, sma


def test_sma() -> None:
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]


def test_ema_seeded_with_sma() -> None:
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]


def test_rsi_hand_computed() -> None:
    # changes +1, -1: avg gain = avg loss = 0.5 -> 50; then +1: gain .75, loss .25 -> 75
    out = rsi([1, 2, 1, 2], 2)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(50)
    assert out[3] == pytest.approx(75)
    assert rsi([1, 2, 3, 4], 2)[-1] == 100


def test_sma_cross_signal() -> None:
    prices = [10.0] * 5 + [20.0] * 3 + [1.0] * 3
    sig = SIGNALS["sma_cross"](prices, {"fast": 2, "slow": 4})
    assert sig == [0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0]


def test_ema_trend_signal() -> None:
    assert SIGNALS["ema_trend"]([1, 2, 3, 4, 1], {"span": 3}) == [0, 0, 1, 1, 0]


def test_momentum_signal_with_skip() -> None:
    # t=3: p[2]/p[0] = 3 > 1 ; t=4: p[3]/p[1] = 1 -> not strictly above
    assert SIGNALS["momentum"]([1, 2, 3, 2, 1], {"lookback": 2, "skip": 1}) == [0, 0, 0, 1, 0]


def test_rsi_reversion_state_machine() -> None:
    # rsi: [-, -, 0, 0, 50, 75, ...]: enter below 5 at t=2, hold at exactly 50, exit above 50 at t=5
    sig = SIGNALS["rsi_reversion"]([10.0, 9, 8, 7, 8, 9, 10], {"period": 2, "entry": 5, "exit": 50})
    assert sig == [0, 0, 1, 1, 1, 0, 0]


def test_donchian_signal() -> None:
    sig = SIGNALS["donchian_breakout"]([1.0, 1, 1, 2, 2, 1, 1], {"entry": 3, "exit": 2})
    assert sig == [0, 0, 0, 1, 1, 0, 0]


@pytest.mark.parametrize("family", sorted(BOUNDS))
def test_every_signal_is_causal(family: str) -> None:
    """The signal at day t cannot depend on later prices: truncating the future changes nothing."""
    rng = random.Random(7)
    prices = [100.0]
    for _ in range(400):
        prices.append(prices[-1] * (1 + rng.gauss(0, 0.02)))
    params = {k: (lo + hi) // 4 for k, (lo, hi) in BOUNDS[family].items()}
    if family == "sma_cross":
        params = {"fast": 10, "slow": 50}
    strat = Strategy("x", "h", family, params)
    full = signals(strat, prices)
    for cut in (120, 250, 333):
        assert signals(strat, prices[:cut]) == full[:cut]

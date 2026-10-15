"""Hand-computed metrics, costs, look-ahead, and the Deflated Sharpe Ratio."""

from __future__ import annotations

import math

import pytest
from choregos_quant import metrics
from choregos_quant.engine import daily_returns, positions_from_signals
from choregos_quant.strategies import Strategy, signals


def test_cagr() -> None:
    assert metrics.cagr([0.1, -0.1]) == pytest.approx(0.99**126 - 1)
    assert metrics.cagr([]) == 0.0


def test_sharpe() -> None:
    # mean .02, sample sd .0141421 -> 1.41421, annualised with sqrt(252)
    assert metrics.sharpe([0.01, 0.03]) == pytest.approx(math.sqrt(2) * math.sqrt(252))
    assert metrics.sharpe([0.01, 0.01, 0.01]) == 0.0


def test_max_drawdown() -> None:
    # equity 1.1 -> 0.55 -> 0.66: drawdown 50 %
    assert metrics.max_drawdown([0.1, -0.5, 0.2]) == pytest.approx(0.5)
    assert metrics.max_drawdown([0.1, 0.1]) == 0.0


def test_skew_kurtosis() -> None:
    assert metrics.skew_kurtosis([1.0, -1.0]) == (0.0, 1.0)
    assert metrics.skew_kurtosis([-1.0, 0.0, 1.0])[1] == pytest.approx(1.5)


def test_expected_max_sharpe_reference() -> None:
    # Reference computed independently (erf-based inverse CDF), N=10, variance 0.01
    assert metrics.expected_max_sharpe(10, 0.01) == pytest.approx(0.15745983, abs=1e-7)
    assert metrics.expected_max_sharpe(1, 0.01) == 0.0


RETURNS = [0.01, 0.02, -0.005, 0.015, 0.0, 0.01]


def test_dsr_single_trial_reference() -> None:
    assert metrics.deflated_sharpe(RETURNS, 1, 0.0) == pytest.approx(0.95345, abs=1e-5)


def test_dsr_deflates_with_more_trials() -> None:
    assert metrics.deflated_sharpe(RETURNS, 10, 0.01) == pytest.approx(0.91680, abs=1e-5)
    assert metrics.deflated_sharpe(RETURNS, 10, 0.01) < metrics.deflated_sharpe(RETURNS, 1, 0.0)


def test_dsr_degenerate_inputs() -> None:
    assert metrics.deflated_sharpe([0.01, 0.01], 5, 0.01) == 0.0
    assert metrics.deflated_sharpe([0.01] * 10, 5, 0.01) == 0.0


def test_positions_are_signals_shifted_one_day() -> None:
    assert positions_from_signals([1, 1, 0, 1]) == [0, 1, 1, 0]


def test_cost_is_paid_on_every_position_change() -> None:
    prices = [100.0] * 4
    pos = [0, 1, 1, 0]
    assert daily_returns(prices, pos, 0.001) == pytest.approx([-0.001, 0.0, -0.001])
    assert daily_returns(prices, pos, 0.0) == [0.0, 0.0, 0.0]


def test_no_look_ahead_jump_on_signal_day_is_not_earned() -> None:
    """The close of day 30 triggers the signal; the +100 % of day 30 must not be earned."""
    prices = [100.0] * 30 + [200.0] * 10
    strat = Strategy("s", "h", "sma_cross", {"fast": 5, "slow": 20})
    sig = signals(strat, prices)
    assert sig[30] == 1  # the signal exists on day 30 ...
    rets = daily_returns(prices, positions_from_signals(sig), 0.0)
    assert rets[29] == 0.0  # ... but day 30's return (index 29) is not earned
    assert sum(rets) == 0.0  # flat afterwards: the jump was never captured

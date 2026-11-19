# SPDX-License-Identifier: Apache-2.0
"""Performance metrics on lists of daily returns (pure functions, 252 days a year, rf = 0)."""

from __future__ import annotations

import math
from statistics import NormalDist

DAYS = 252
EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


def mean(x: list[float]) -> float:
    return sum(x) / len(x) if x else 0.0


def stdev(x: list[float]) -> float:
    """Sample standard deviation (n - 1)."""
    if len(x) < 2:
        return 0.0
    m = mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def cagr(returns: list[float]) -> float:
    if not returns:
        return 0.0
    growth = 1.0
    for r in returns:
        growth *= 1 + r
    if growth <= 0:
        return -1.0
    return float(growth ** (DAYS / len(returns))) - 1


def sharpe_period(returns: list[float]) -> float:
    """Per-period (not annualised) Sharpe; 0 when the volatility is 0."""
    s = stdev(returns)
    return mean(returns) / s if s > 1e-15 else 0.0


def sharpe(returns: list[float]) -> float:
    return sharpe_period(returns) * math.sqrt(DAYS)


def max_drawdown(returns: list[float]) -> float:
    """Largest peak-to-trough fall of the equity curve (starting at 1), as a positive fraction."""
    equity = peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= 1 + r
        peak = max(peak, equity)
        worst = max(worst, 1 - equity / peak)
    return worst


def skew_kurtosis(returns: list[float]) -> tuple[float, float]:
    """Population skewness and (non-excess) kurtosis; (0, 3) when the variance is 0."""
    n = len(returns)
    if n < 2:
        return 0.0, 3.0
    m = mean(returns)
    m2 = sum((v - m) ** 2 for v in returns) / n
    if m2 <= 1e-30:
        return 0.0, 3.0
    m3 = sum((v - m) ** 3 for v in returns) / n
    m4 = sum((v - m) ** 4 for v in returns) / n
    return m3 / m2**1.5, m4 / m2**2


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """SR0 of Bailey & Lopez de Prado (2014): expected best Sharpe of `n_trials` unskilled trials."""
    if n_trials <= 1 or sr_variance <= 0:
        return 0.0
    a = _N.inv_cdf(1 - 1 / n_trials)
    b = _N.inv_cdf(1 - 1 / (n_trials * math.e))
    return math.sqrt(sr_variance) * ((1 - EULER_GAMMA) * a + EULER_GAMMA * b)


def deflated_sharpe(returns: list[float], n_trials: int, sr_variance: float) -> float:
    """Deflated Sharpe Ratio: probability that the true Sharpe beats the best of `n_trials` luck.

    `returns` are the out-of-sample daily returns of the candidate; `sr_variance` is the variance of
    the per-period Sharpe ratios across all trials. Skewness and kurtosis come from `returns`.
    """
    t = len(returns)
    if t < 3:
        return 0.0
    sr = sharpe_period(returns)
    if sr == 0.0 and stdev(returns) <= 1e-15:
        return 0.0
    skew, kurt = skew_kurtosis(returns)
    denominator = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if denominator <= 0:
        return 0.0
    z = (sr - expected_max_sharpe(n_trials, sr_variance)) * math.sqrt(t - 1) / math.sqrt(denominator)
    return _N.cdf(z)

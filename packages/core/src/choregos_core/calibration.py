# SPDX-License-Identifier: Apache-2.0
"""Calibration measured from the decision ledger, never declared (ADR 0121 §7).

A probability is calibrated when, among the decisions given about 0.8, about 80 % turn out right.
From the observed decisions of one question (each a probability and whether it was right, see
`choregos_core.decision_ledger`), this module computes:

- the **reliability bins**: ten bins of probability, `[0, 0.1)` … `[0.9, 1]`, each with its count,
  its mean probability and the share of its decisions that were right;
- the **expected calibration error** (ECE): the bins' gap between mean probability and accuracy,
  weighted by their counts — 0 for a perfectly calibrated model;
- for each **threshold** from 0.50 to 0.95 by 0.05, the **coverage** (the share of observed decisions
  at or above it) and the **accuracy** among them;
- a **suggested threshold**: the lowest one whose accuracy reaches the target, with at least
  `MIN_ABOVE` observed decisions at or above it — and none, with the reason, while fewer than
  `MIN_OBSERVED` decisions are observed, or when no threshold reaches the target.

Pure: no I/O, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

#: The reliability diagram's bins: ten of equal width over [0, 1].
BINS = 10
#: The thresholds a coverage and an accuracy are given at: 0.50, 0.55, … 0.95.
THRESHOLDS: tuple[float, ...] = tuple(round(0.5 + 0.05 * step, 2) for step in range(10))
#: The observed decisions below which no threshold is suggested: fewer say more about chance than
#: about the model (a 90 % target read on 10 decisions is one decision either way).
MIN_OBSERVED = 30
#: The observed decisions a threshold must keep at or above it to be suggested: a threshold that keeps
#: three decisions, all right, proves nothing.
MIN_ABOVE = 10
#: The accuracy a suggested threshold must reach when the caller names none.
DEFAULT_TARGET = 0.9
#: How many decimals the figures keep.
DIGITS = 4


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    """One bin of probability: `[lower, upper)`, the last one closed."""

    lower: float
    upper: float
    count: int
    mean_probability: float | None
    accuracy: float | None


@dataclass(frozen=True, slots=True)
class ThresholdPoint:
    """What a threshold keeps: how many observed decisions, which share, how many right."""

    threshold: float
    count: int
    coverage: float | None
    accuracy: float | None


@dataclass(frozen=True, slots=True)
class Calibration:
    """One question's calibration, from its recorded decisions."""

    count: int
    observed: int
    accuracy: float | None
    ece: float | None
    bins: list[ReliabilityBin] = field(default_factory=list)
    thresholds: list[ThresholdPoint] = field(default_factory=list)
    suggested_threshold: float | None = None
    suggestion_reason: str | None = None


def bin_of(probability: float) -> int:
    """The bin a probability falls in; 1.0 falls in the last one."""
    return min(max(int(probability * BINS), 0), BINS - 1)


def _binned(observed: list[tuple[float, bool]]) -> list[list[tuple[float, bool]]]:
    grouped: list[list[tuple[float, bool]]] = [[] for _ in range(BINS)]
    for probability, right in observed:
        grouped[bin_of(probability)].append((probability, right))
    return grouped


def reliability_bins(observed: list[tuple[float, bool]]) -> list[ReliabilityBin]:
    """The ten bins over the observed decisions; an empty bin has no mean and no accuracy."""
    return [
        ReliabilityBin(
            lower=round(index / BINS, 2),
            upper=round((index + 1) / BINS, 2),
            count=len(members),
            mean_probability=_mean([p for p, _ in members]),
            accuracy=_mean([1.0 if right else 0.0 for _, right in members]),
        )
        for index, members in enumerate(_binned(observed))
    ]


def expected_calibration_error(observed: list[tuple[float, bool]]) -> float | None:
    """Σ over the bins of (count / total) × |accuracy − mean probability|; `None` with nothing observed."""
    if not observed:
        return None
    total = len(observed)
    gap = 0.0
    for members in _binned(observed):
        if members:
            mean_probability = sum(p for p, _ in members) / len(members)
            accuracy = sum(1 for _, right in members if right) / len(members)
            gap += len(members) / total * abs(accuracy - mean_probability)
    return round(gap, DIGITS)


def threshold_points(observed: list[tuple[float, bool]]) -> list[ThresholdPoint]:
    """Coverage and accuracy at or above each threshold of `THRESHOLDS`."""
    points: list[ThresholdPoint] = []
    for threshold in THRESHOLDS:
        kept = [right for probability, right in observed if probability >= threshold - 1e-9]
        points.append(
            ThresholdPoint(
                threshold=threshold,
                count=len(kept),
                coverage=round(len(kept) / len(observed), DIGITS) if observed else None,
                accuracy=_mean([1.0 if right else 0.0 for right in kept]),
            )
        )
    return points


def suggest_threshold(
    observed: list[tuple[float, bool]],
    target: float = DEFAULT_TARGET,
    *,
    min_observed: int = MIN_OBSERVED,
    min_above: int = MIN_ABOVE,
) -> tuple[float | None, str | None]:
    """The lowest threshold whose accuracy reaches `target` on at least `min_above` decisions, or
    `None` and why."""
    if len(observed) < min_observed:
        return None, (
            f"{len(observed)} observed decision(s): a threshold is suggested from {min_observed} on"
        )
    for point in threshold_points(observed):
        if point.count >= min_above and point.accuracy is not None and point.accuracy >= target:
            return point.threshold, None
    return None, (
        f"no threshold from {THRESHOLDS[0]:.2f} to {THRESHOLDS[-1]:.2f} reaches {target:.0%} accuracy "
        f"on at least {min_above} observed decisions"
    )


def calibrate(
    decisions: Iterable[tuple[float, bool | None]],
    target: float = DEFAULT_TARGET,
    *,
    min_observed: int = MIN_OBSERVED,
    min_above: int = MIN_ABOVE,
) -> Calibration:
    """One question's calibration from its decisions: `(probability, right)`, `right` `None` while
    the decision's outcome is not observed."""
    every = list(decisions)
    observed = [(probability, right) for probability, right in every if right is not None]
    suggested, reason = suggest_threshold(observed, target, min_observed=min_observed, min_above=min_above)
    return Calibration(
        count=len(every),
        observed=len(observed),
        accuracy=_mean([1.0 if right else 0.0 for _, right in observed]),
        ece=expected_calibration_error(observed),
        bins=reliability_bins(observed),
        thresholds=threshold_points(observed),
        suggested_threshold=suggested,
        suggestion_reason=reason,
    )


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), DIGITS) if values else None


__all__ = [
    "BINS",
    "DEFAULT_TARGET",
    "MIN_ABOVE",
    "MIN_OBSERVED",
    "THRESHOLDS",
    "Calibration",
    "ReliabilityBin",
    "ThresholdPoint",
    "bin_of",
    "calibrate",
    "expected_calibration_error",
    "reliability_bins",
    "suggest_threshold",
    "threshold_points",
]

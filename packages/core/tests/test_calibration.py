"""Calibration measured from the ledger (ADR 0121 §7, S52-05): bins, ECE, thresholds, a suggestion."""

from __future__ import annotations

import pytest
from choregos_core.calibration import (
    MIN_ABOVE,
    MIN_OBSERVED,
    THRESHOLDS,
    bin_of,
    calibrate,
    expected_calibration_error,
    reliability_bins,
    suggest_threshold,
    threshold_points,
)


def _at(probability: float, count: int, right: int) -> list[tuple[float, bool]]:
    """`count` decisions given `probability`, `right` of them right."""
    return [(probability, index < right) for index in range(count)]


def _calibrated() -> list[tuple[float, bool]]:
    """Ten bins, each at its centre, right exactly as often as the probability says."""
    decisions: list[tuple[float, bool]] = []
    for step in range(10):
        probability = round(0.05 + 0.1 * step, 2)
        decisions += _at(probability, 100, round(100 * probability))
    return decisions


def test_the_thresholds_run_from_one_half_to_0_95_by_0_05() -> None:
    assert THRESHOLDS == (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95)


@pytest.mark.parametrize(
    ("probability", "index"), [(0.0, 0), (0.0999, 0), (0.1, 1), (0.55, 5), (0.7, 7), (0.9999, 9), (1.0, 9)]
)
def test_a_probability_falls_in_its_tenth_and_1_in_the_last(probability: float, index: int) -> None:
    assert bin_of(probability) == index


def test_a_perfectly_calibrated_model_has_no_calibration_error() -> None:
    decisions = _calibrated()
    assert expected_calibration_error(decisions) == pytest.approx(0.0, abs=1e-9)
    bins = reliability_bins(decisions)
    assert [b.count for b in bins] == [100] * 10
    for bin_ in bins:
        assert bin_.mean_probability == pytest.approx(bin_.accuracy)
        assert bin_.lower <= float(bin_.mean_probability or 0) < bin_.upper


def test_an_over_confident_model_has_a_large_calibration_error() -> None:
    """Says 0.95 and is right half the time, as Laya's checkpoints are said to be as shipped."""
    decisions = _at(0.95, 200, 100)
    assert expected_calibration_error(decisions) == pytest.approx(0.45)
    bins = reliability_bins(decisions)
    assert bins[9].count == 200 and bins[9].accuracy == 0.5 and bins[9].mean_probability == 0.95
    assert all(b.count == 0 and b.accuracy is None and b.mean_probability is None for b in bins[:9])


def test_an_under_confident_model_errs_the_other_way_by_the_same_measure() -> None:
    assert expected_calibration_error(_at(0.55, 100, 95)) == pytest.approx(0.40)


def test_the_error_weighs_each_bin_by_its_count() -> None:
    # 90 perfect decisions at 0.85 (bin 8), 10 at 0.95 all wrong (bin 9): 0.1 × 0.95.
    decisions = [(0.85, index < 765) for index in range(900)] + _at(0.95, 100, 0)
    assert expected_calibration_error(decisions) == pytest.approx(0.1 * 0.95, abs=1e-4)


def test_nothing_observed_gives_no_figure() -> None:
    calibration = calibrate([])
    assert (calibration.count, calibration.observed) == (0, 0)
    assert calibration.accuracy is None and calibration.ece is None
    assert [b.count for b in calibration.bins] == [0] * 10
    assert all(p.count == 0 and p.coverage is None and p.accuracy is None for p in calibration.thresholds)
    assert calibration.suggested_threshold is None
    assert calibration.suggestion_reason == "0 observed decision(s): a threshold is suggested from 30 on"


def test_unobserved_decisions_are_counted_and_measure_nothing() -> None:
    calibration = calibrate([(0.9, None), (0.9, None), (0.6, True)])
    assert (calibration.count, calibration.observed) == (3, 1)
    assert calibration.accuracy == 1.0
    assert calibration.ece == pytest.approx(0.4)


def test_coverage_and_accuracy_at_each_threshold() -> None:
    # 20 at 0.55 (half right), 20 at 0.75 (15 right), 20 at 0.95 (19 right).
    decisions = _at(0.55, 20, 10) + _at(0.75, 20, 15) + _at(0.95, 20, 19)
    points = {p.threshold: p for p in threshold_points(decisions)}
    assert points[0.5].count == 60 and points[0.5].coverage == 1.0
    assert points[0.5].accuracy == pytest.approx(44 / 60, abs=1e-4)
    assert points[0.55].count == 60, "a decision AT the threshold is kept"
    assert points[0.6].count == 40 and points[0.6].coverage == pytest.approx(2 / 3, abs=1e-4)
    assert points[0.6].accuracy == pytest.approx(34 / 40)
    assert points[0.75].count == 40
    assert points[0.8].count == 20 and points[0.8].accuracy == 0.95
    assert points[0.95].count == 20 and points[0.95].coverage == pytest.approx(1 / 3, abs=1e-4)


def test_the_suggestion_is_the_lowest_threshold_that_reaches_the_target() -> None:
    decisions = _at(0.55, 20, 10) + _at(0.75, 20, 15) + _at(0.95, 20, 19)
    assert suggest_threshold(decisions, 0.9) == (0.8, None)
    assert suggest_threshold(decisions, 0.8) == (0.6, None)
    assert suggest_threshold(decisions, 0.7) == (0.5, None)


def test_no_threshold_is_suggested_below_the_minimum_sample() -> None:
    decisions = _at(0.95, MIN_OBSERVED - 1, MIN_OBSERVED - 1)
    threshold, reason = suggest_threshold(decisions, 0.9)
    assert threshold is None
    assert (
        reason == f"{MIN_OBSERVED - 1} observed decision(s): a threshold is suggested from {MIN_OBSERVED} on"
    )
    assert suggest_threshold(_at(0.95, MIN_OBSERVED, MIN_OBSERVED), 0.9) == (0.5, None)


def test_a_threshold_that_keeps_too_few_decisions_is_not_suggested() -> None:
    """At 0.95, nine decisions all right: not enough to stand on; the others are too often wrong."""
    decisions = _at(0.6, 40, 20) + _at(0.95, MIN_ABOVE - 1, MIN_ABOVE - 1)
    threshold, reason = suggest_threshold(decisions, 0.9)
    assert threshold is None
    assert reason == "no threshold from 0.50 to 0.95 reaches 90% accuracy on at least 10 observed decisions"
    decisions += [(0.95, True)]
    # Ten now, all right: the lowest threshold that keeps only them is above the 0.6 decisions.
    assert suggest_threshold(decisions, 0.9) == (0.65, None)


def test_calibrate_puts_the_figures_together() -> None:
    decisions: list[tuple[float, bool | None]] = [*_calibrated(), (0.9, None)]
    calibration = calibrate(decisions, 0.8)
    assert calibration.count == 1001 and calibration.observed == 1000
    assert calibration.accuracy == 0.5
    assert calibration.ece == pytest.approx(0.0, abs=1e-9)
    assert len(calibration.bins) == 10 and len(calibration.thresholds) == 10
    # At 0.55: (55 + 65 + 75 + 85 + 95) / 500 = 0.75; at 0.6: (65 + 75 + 85 + 95) / 400 = 0.8 exactly.
    assert calibration.suggested_threshold == 0.6
    assert calibration.suggestion_reason is None

"""The matrix compares the two doors (S57-09, ADR 0122 §9): a paired bootstrap 95 % interval of the
relative difference in cost per successful run and of the difference in success rate, and a verdict.

The small fixtures are computed by hand: their bootstrap distribution can be enumerated, so the
interval the 10,000 resamples give is known before they are drawn.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

import pytest
from choregos_orchestrator import eval_comparison as comparison
from choregos_orchestrator.eval_comparison import Unit


def _unit(
    case: str, ok_mcp: bool, ok_scripts: bool, cost_mcp: float, cost_scripts: float, **extra: Any
) -> Unit:
    return Unit(
        case=case,
        repetition=int(extra.get("repetition", 0)),
        agent=str(extra.get("agent", "netops")),
        ok_baseline=ok_mcp,
        ok_candidate=ok_scripts,
        cost_baseline=cost_mcp,
        cost_candidate=cost_scripts,
    )


def test_the_interval_s_ranks_are_the_nearest_ranks_in_integers() -> None:
    # 0.025 × 10,000 and 0.975 × 10,000, without the float that would make the first 251.
    assert (comparison.rank(10_000, comparison.LOWER), comparison.rank(10_000, comparison.UPPER)) == (
        250,
        9750,
    )
    assert (comparison.rank(3, comparison.LOWER), comparison.rank(3, comparison.UPPER)) == (1, 3)
    assert (comparison.rank(40, comparison.LOWER), comparison.rank(40, comparison.UPPER)) == (1, 39)
    assert comparison.interval([5.0, 1.0, 3.0, 2.0, 4.0] * 8) == (1.0, 5.0)


def test_the_interval_matches_the_one_computed_by_hand() -> None:
    """Three cases: `scripts` succeeds where `mcp` failed on the first, both succeed on the others.

    A resample of three draws with replacement holds k copies of the first case with probability
    C(3, k) (1/3)^k (2/3)^(3-k): 8/27 (k=0), 12/27, 6/27 and 1/27 (k=3), and its success difference
    is k/3. The 2.5th percentile is therefore 0 (8/27 of the mass), and the 97.5th is 1: the mass
    below 1 is 26/27 = 0.963 < 0.975. The interval is [0, 1], the estimate 1/3."""
    units = [
        _unit("a", False, True, 1.0, 1.0),
        _unit("b", True, True, 1.0, 1.0),
        _unit("c", True, True, 1.0, 1.0),
    ]
    (successes,) = comparison.resampled(units, [comparison.success_difference])
    assert comparison.interval(successes) == (0.0, 1.0)
    # The draws follow the exact distribution: each frequency within a point of its probability.
    seen = Counter(round(value * 3) for value in successes)
    for copies, probability in {0: 8 / 27, 1: 12 / 27, 2: 6 / 27, 3: 1 / 27}.items():
        assert abs(seen[copies] / comparison.RESAMPLES - probability) < 0.01, (copies, seen)
    result = comparison.compare(units)
    assert result["success_difference"] == {"estimate": 0.333333, "low": 0.0, "high": 1.0}


def test_the_cost_per_successful_run_is_compared_by_hand() -> None:
    """Every case costs $1.00 by MCP and $0.40 by script, and succeeds both ways: every resample has a
    cost per success of 0.40 against 1.00, a relative difference of −0.6."""
    units = [_unit(f"c{n}", True, True, 1.0, 0.4) for n in range(4)]
    result = comparison.compare(units)
    assert result["cost_relative_difference"] == {"estimate": -0.6, "low": -0.6, "high": -0.6}
    assert result["mcp"] == {"success_rate": 1.0, "cost_per_success_usd": 1.0}
    assert result["scripts"] == {"success_rate": 1.0, "cost_per_success_usd": 0.4}
    assert result["verdict"] == "better"


def test_the_bootstrap_is_deterministic() -> None:
    """The same units and seed give the same interval, every time; the seed is what fixes it."""
    units = [
        _unit(f"c{n % 5}", n % 4 != 0, n % 3 != 0, 0.3 + n / 100, 0.1 + n / 200, repetition=n)
        for n in range(20)
    ]
    first, again = comparison.compare(units), comparison.compare(units)
    assert first == again
    assert first["seed"] == comparison.SEED and first["resamples"] == 10_000
    other = comparison.compare(units, seed=comparison.SEED + 1)
    assert other["success_difference"]["estimate"] == first["success_difference"]["estimate"]
    assert (other["success_difference"], other["cost_relative_difference"]) != (
        first["success_difference"],
        first["cost_relative_difference"],
    ), "another seed draws other resamples"


@pytest.mark.parametrize(
    ("success", "cost", "expected"),
    [
        ((0.0, 0.0), (-0.6, -0.1), "better"),
        ((-0.05, 0.1), (-0.6, -0.1), "better"),  # five points lower is tolerated (§5.2)
        ((-0.06, 0.1), (-0.6, -0.1), "inconclusive"),  # cheaper, but the rate may have dropped
        ((-0.5, -0.1), (-0.6, -0.1), "worse"),  # cheaper and worse
        ((0.0, 0.0), (0.05, 0.2), "worse"),  # as good, dearer
        ((0.0, 0.0), (0.0, 0.0), "not_worse"),  # a tie
        ((-0.02, 0.03), (-0.1, 0.2), "not_worse"),  # cost holds 0, success holds
        ((-0.3, 0.2), (-0.1, 0.2), "inconclusive"),
        ((0.0, 0.0), (-0.2, math.inf), "not_worse"),
    ],
)
def test_the_verdicts(success: tuple[float, float], cost: tuple[float, float], expected: str) -> None:
    assert comparison.verdict(success, cost) == expected


@pytest.mark.parametrize(
    ("units", "expected"),
    [
        ([_unit("a", False, False, 1.0, 1.0)], 0.0),  # neither door succeeded: neither is cheaper
        ([_unit("a", True, False, 1.0, 1.0)], math.inf),  # only `mcp` succeeded
        ([_unit("a", False, True, 1.0, 1.0)], -1.0),  # only `scripts` succeeded
        ([_unit("a", True, True, 0.0, 0.0)], 0.0),  # both free
        ([_unit("a", True, True, 0.0, 0.2)], math.inf),
        # A failed run's cost still counts: $0.50 for one success against $2.00 for two.
        ([_unit("a", True, True, 1.0, 0.25), _unit("b", True, False, 1.0, 0.25)], -0.5),
    ],
)
def test_the_relative_cost_difference_s_edges(units: list[Unit], expected: float) -> None:
    assert comparison.relative_cost_difference(units) == pytest.approx(expected)


def test_an_unbounded_interval_is_published_as_null() -> None:
    units = [_unit("a", True, True, 1.0, 0.5), _unit("b", True, False, 1.0, 0.5)]
    result = comparison.compare(units)
    assert result["cost_relative_difference"]["high"] is None, "a resample where `scripts` never succeeds"
    assert result["verdict"] == "inconclusive"


def test_an_unmetered_pair_is_never_the_cheaper_one() -> None:
    """A cell nothing was billed for has no cost, not a cost of 0: without that, a `scripts` cell missing
    its usage would read as free, hence `better`."""
    free = [_unit("a", True, True, 1.0, None), _unit("b", True, True, 1.0, 0.2)]
    result = comparison.compare(free)
    assert result["verdict"] == "inconclusive" and result["unmetered_pairs"] == 1
    assert result["cost_relative_difference"] == {"estimate": None, "low": None, "high": None}
    assert result["scripts"]["cost_per_success_usd"] is None
    failing = [_unit(f"c{n}", True, False, 1.0, None) for n in range(4)]
    assert comparison.compare(failing)["verdict"] == "worse", "worse by success needs no cost"


def _cell(tools: str, details: list[dict[str, Any]], **where: Any) -> dict[str, Any]:
    return {
        "backend": "claude-code",
        "model": "platform/standard",
        "with_memory": False,
        "context": "none",
        "tools": tools,
        "details": details,
        **where,
    }


def test_cells_differing_only_by_their_door_are_compared_by_agent_and_by_case() -> None:
    mcp = _cell(
        "mcp",
        [
            {"fixture": "e1", "repetition": 0, "agent": "netops", "ok": True, "cost_usd": 0.5},
            {"fixture": "e1", "repetition": 1, "agent": "netops", "ok": True, "cost_usd": 0.5},
            {"fixture": "e4", "repetition": 0, "agent": "identity-operator", "ok": True, "cost_usd": 0.2},
            {"fixture": "gone", "repetition": 0, "agent": "netops", "skipped": True, "reason": "x"},
        ],
    )
    scripts = _cell(
        "scripts",
        [
            {"fixture": "e1", "repetition": 0, "agent": "netops", "ok": True, "cost_usd": 0.1},
            {"fixture": "e1", "repetition": 1, "agent": "netops", "ok": True, "cost_usd": 0.1},
            {"fixture": "e4", "repetition": 0, "agent": "identity-operator", "ok": False, "cost_usd": 0.1},
            {"fixture": "gone", "repetition": 0, "agent": "netops", "ok": True, "cost_usd": 0.1},
        ],
    )
    other_context = _cell(
        "mcp", [{"fixture": "e1", "repetition": 0, "ok": True, "cost_usd": 1.0}], context="rtk"
    )
    found = comparison.compare_cells([mcp, scripts, other_context])
    assert [(c["group"], c["key"], c["pairs"], c["verdict"]) for c in found] == [
        ("agent", "netops", 2, "better"),  # `gone` is skipped in `mcp`: no pair
        ("agent", "identity-operator", 1, "worse"),
        ("case", "e1", 2, "better"),
        ("case", "e4", 1, "worse"),
    ]
    assert {(c["baseline"], c["candidate"], c["context"]) for c in found} == {("mcp", "scripts", "none")}
    assert comparison.compare_cells([mcp, other_context]) == [], "no `scripts` cell, nothing to compare"
    assert comparison.compare([])["verdict"] == "inconclusive"

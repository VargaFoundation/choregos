# SPDX-License-Identifier: Apache-2.0
"""Two cells of the eval matrix that differ only by their tools' door, compared (S57-09, ADR 0122 §9).

The gate of ADR 0122 §9 (the plan's §5.2): an agent's catalogue entry may offer tool scripts by default
only when `scripts` shows, against `mcp`, a lower cost per successful run AND a success rate that is
not lower, with a paired bootstrap 95 % interval computed by the matrix itself. ADR 0055's interval was
computed by hand; this is the matrix's own, pure Python, deterministic.

**Pairs.** A unit is a case and a repetition (`(fixture, repetition)`) played, not skipped and not invalid
(S57-11a: its governance parity broken), in BOTH cells.
A unit is metered when the gateway counted its run in both cells (`cost_usd` is not `None`). When one
unit of a comparison is not metered, its cost is unknown, not zero: the cost interval is not computed
(`null`), and the verdict can only be `worse` by success, else `inconclusive` — a cell whose cost was
not measured is never the cheaper one.

**Statistics**, over a list of units:

- success difference: `success rate(scripts) − success rate(mcp)`, in points of rate (−1 … 1);
- relative difference in cost per successful run: `cps(scripts) / cps(mcp) − 1`, where
  `cps = Σ cost / successful runs`. A cell without a successful run has an infinite cost per success:
  the difference is `+∞` when only `scripts` has none, `−1` when only `mcp` has none, and 0 when neither
  has one (neither door succeeded, neither is cheaper per success); `mcp` at a cost of 0 gives 0 when
  `scripts` costs 0 too, `+∞` otherwise.

**Bootstrap.** `RESAMPLES` (10,000) resamples of the units, with replacement, the same resample for both
statistics (paired: a unit brings its two cells together). The draws come from `random.Random(SEED)`
and only from its `random()` (`int(random() * n)`), whose sequence Python keeps for a given seed: the
same units give the same interval on every machine and version. The 95 % interval is the nearest-rank
2.5th and 97.5th percentiles of the sorted resampled statistics: ranks `ceil(0.025 R)` and
`ceil(0.975 R)` (250 and 9,750 of 10,000), computed in integers.

**Verdict**, with `MARGIN` = 0.05 (five points of success rate), checked in this order:

1. `worse` — the success interval lies entirely below −0.05 (its upper bound < −0.05), or the cost
   interval lies entirely above 0 (its lower bound > 0);
2. `better` — the cost interval lies entirely below 0 (its upper bound < 0) and the success interval's
   lower bound is ≥ −0.05;
3. `not_worse` — the success interval's lower bound is ≥ −0.05, the cost interval holds 0;
4. `inconclusive` — anything else, a comparison without a single pair, and one with an unmetered pair
   that the success interval does not already call `worse`.

In the published payload an infinite bound is `null` (unbounded).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

RESAMPLES = 10_000
#: Fixed: the same matrix gives the same interval.
SEED = 5709
#: Five points of success rate: what `not_worse` tolerates (§5.2).
MARGIN = 0.05
#: The interval's ranks as fractions (numerator, denominator): exact, no float on the way.
LOWER, UPPER = (25, 1000), (975, 1000)
BASELINE, CANDIDATE = "mcp", "scripts"
VERDICTS = ("better", "not_worse", "worse", "inconclusive")
#: What tells two runs apart in one comparison: the differences of rates are multiples of 1/n.
_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Unit:
    """One case and repetition, played in both cells."""

    case: str
    repetition: int
    agent: str
    ok_baseline: bool
    ok_candidate: bool
    #: `None`: the gateway counted nothing for the run (not metered).
    cost_baseline: float | None
    cost_candidate: float | None

    @property
    def metered(self) -> bool:
        return self.cost_baseline is not None and self.cost_candidate is not None


def cost_per_success(costs: Iterable[float | None], oks: Iterable[bool]) -> float:
    """Σ cost / successful runs; infinite without a successful run. An unmetered cost counts 0: the
    callers compare only metered units."""
    successes = sum(1 for ok in oks if ok)
    total = sum(cost or 0.0 for cost in costs)
    return total / successes if successes else math.inf


def relative_cost_difference(units: Sequence[Unit]) -> float:
    """`cps(scripts) / cps(mcp) − 1`, with the module's rules for the infinite and the free."""
    baseline = cost_per_success((u.cost_baseline for u in units), (u.ok_baseline for u in units))
    candidate = cost_per_success((u.cost_candidate for u in units), (u.ok_candidate for u in units))
    if math.isinf(baseline) and math.isinf(candidate):
        return 0.0
    if math.isinf(candidate):
        return math.inf
    if math.isinf(baseline):
        return -1.0
    if baseline == 0:
        return 0.0 if candidate == 0 else math.inf
    return candidate / baseline - 1.0


def success_difference(units: Sequence[Unit]) -> float:
    """`rate(scripts) − rate(mcp)`."""
    if not units:
        return 0.0
    return (sum(u.ok_candidate for u in units) - sum(u.ok_baseline for u in units)) / len(units)


def rank(count: int, fraction: tuple[int, int]) -> int:
    """The nearest rank of a percentile, from 1: `ceil(count × numerator / denominator)`, at least 1."""
    numerator, denominator = fraction
    return max(1, -(-count * numerator // denominator))


def resampled(
    units: Sequence[Unit],
    statistics: Sequence[Callable[[Sequence[Unit]], float]],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> list[list[float]]:
    """Each statistic over `resamples` resamples of `units`, the same resample for every statistic."""
    rng = random.Random(seed)  # noqa: S311 - a bootstrap draws resamples, it keeps no secret
    count = len(units)
    values: list[list[float]] = [[] for _ in statistics]
    for _ in range(resamples):
        sample = [units[int(rng.random() * count)] for _ in range(count)]
        for index, statistic in enumerate(statistics):
            values[index].append(statistic(sample))
    return values


def interval(values: Sequence[float]) -> tuple[float, float]:
    """The nearest-rank 2.5th and 97.5th percentiles."""
    ordered = sorted(values)
    return ordered[rank(len(ordered), LOWER) - 1], ordered[rank(len(ordered), UPPER) - 1]


def verdict(success: tuple[float, float], cost: tuple[float, float]) -> str:
    """The module's four verdicts, in their order."""
    success_low, success_high = success
    cost_low, cost_high = cost
    if success_high < -MARGIN - _TOLERANCE or cost_low > 0:
        return "worse"
    holds = success_low >= -MARGIN - _TOLERANCE
    if holds and cost_high < 0:
        return "better"
    if holds:
        return "not_worse"
    return "inconclusive"


def _bound(value: float) -> float | None:
    return None if math.isinf(value) else round(value, 6)


def compare(units: Sequence[Unit], *, resamples: int = RESAMPLES, seed: int = SEED) -> dict[str, Any]:
    """The comparison of one group of units: both cells' rate and cost per success, the two intervals
    and the verdict."""
    common: dict[str, Any] = {"pairs": len(units), "resamples": resamples, "seed": seed, "margin": MARGIN}
    if not units:
        return {**common, "verdict": "inconclusive", "reason": "no case was played in both cells"}
    unmetered = sum(1 for u in units if not u.metered)
    statistics: list[Callable[[Sequence[Unit]], float]] = [success_difference]
    if not unmetered:
        statistics.append(relative_cost_difference)
    drawn = resampled(units, statistics, resamples=resamples, seed=seed)
    success_interval = interval(drawn[0])
    baseline_cps = cost_per_success((u.cost_baseline for u in units), (u.ok_baseline for u in units))
    candidate_cps = cost_per_success((u.cost_candidate for u in units), (u.ok_candidate for u in units))
    result: dict[str, Any] = {
        **common,
        BASELINE: {
            "success_rate": round(sum(u.ok_baseline for u in units) / len(units), 4),
            "cost_per_success_usd": None if unmetered else _bound(baseline_cps),
        },
        CANDIDATE: {
            "success_rate": round(sum(u.ok_candidate for u in units) / len(units), 4),
            "cost_per_success_usd": None if unmetered else _bound(candidate_cps),
        },
        "success_difference": {
            "estimate": round(success_difference(units), 6),
            "low": round(success_interval[0], 6),
            "high": round(success_interval[1], 6),
        },
    }
    if unmetered:
        worse = success_interval[1] < -MARGIN - _TOLERANCE
        return {
            **result,
            "cost_relative_difference": {"estimate": None, "low": None, "high": None},
            "unmetered_pairs": unmetered,
            "verdict": "worse" if worse else "inconclusive",
            "reason": f"{unmetered} of {len(units)} pairs not metered in both cells: their cost is unknown",
        }
    cost_interval = interval(drawn[1])
    return {
        **result,
        "cost_relative_difference": {
            "estimate": _bound(relative_cost_difference(units)),
            "low": _bound(cost_interval[0]),
            "high": _bound(cost_interval[1]),
        },
        "verdict": verdict(success_interval, cost_interval),
    }


def _cell_key(cell: dict[str, Any]) -> tuple[Any, ...]:
    return (cell.get("backend"), cell.get("model"), cell.get("with_memory"), cell.get("context", "none"))


def _units(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[Unit]:
    """The cases and repetitions played, not skipped and not invalid, in both cells, in the baseline's
    order. An invalid run (its governance parity broken, S57-11a) is in no pair: neither door's numbers
    are trusted for it."""

    def played(cell: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
        return {
            (str(d["fixture"]), int(d.get("repetition", 0))): d
            for d in cell.get("details") or []
            if not d.get("skipped") and not d.get("invalid")
        }

    theirs = played(candidate)
    units: list[Unit] = []
    for key, mine in played(baseline).items():
        other = theirs.get(key)
        if other is None:
            continue
        units.append(
            Unit(
                case=key[0],
                repetition=key[1],
                agent=str(mine.get("agent") or "unknown"),
                ok_baseline=bool(mine.get("ok")),
                ok_candidate=bool(other.get("ok")),
                cost_baseline=None if mine.get("cost_usd") is None else float(mine["cost_usd"]),
                cost_candidate=None if other.get("cost_usd") is None else float(other["cost_usd"]),
            )
        )
    return units


def compare_cells(
    results: Sequence[dict[str, Any]], *, resamples: int = RESAMPLES, seed: int = SEED
) -> list[dict[str, Any]]:
    """Every pair of cells that differ only by their door (`tools`: `mcp` against `scripts`), compared
    per agent and per case. A matrix without a `scripts` cell has nothing to compare."""
    by_key: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for cell in results:
        by_key.setdefault(_cell_key(cell), {})[str(cell.get("tools", BASELINE))] = cell
    comparisons: list[dict[str, Any]] = []
    for key, cells in by_key.items():
        if BASELINE not in cells or CANDIDATE not in cells:
            continue
        units = _units(cells[BASELINE], cells[CANDIDATE])
        backend, model, with_memory, context = key
        where = {
            "backend": backend,
            "model": model,
            "with_memory": with_memory,
            "context": context,
            "baseline": BASELINE,
            "candidate": CANDIDATE,
        }
        groups: tuple[tuple[str, Callable[[Unit], str]], ...] = (
            ("agent", lambda u: u.agent),
            ("case", lambda u: u.case),
        )
        for group, of in groups:
            for name in dict.fromkeys(of(u) for u in units):
                chosen = [u for u in units if of(u) == name]
                comparisons.append(
                    {**where, "group": group, "key": name, **compare(chosen, resamples=resamples, seed=seed)}
                )
    return comparisons


__all__ = [
    "BASELINE",
    "CANDIDATE",
    "MARGIN",
    "RESAMPLES",
    "SEED",
    "VERDICTS",
    "Unit",
    "compare",
    "compare_cells",
    "cost_per_success",
    "interval",
    "rank",
    "relative_cost_difference",
    "resampled",
    "success_difference",
    "verdict",
]

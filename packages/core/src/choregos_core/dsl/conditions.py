# SPDX-License-Identifier: Apache-2.0
"""Whether a transition's `when` holds on the outputs of the last stage (S48-01, ADR 0106).

An agent answers in outputs (`outcome: pr`, `done: false`, `confidence: 0.82`, `findings: 3`); a
workflow chooses its next step from them. The comparison is deliberately forgiving about how a value
was written, because agents write JSON loosely: a number written as a string compares as a number,
`"true"`/`"false"` compare as booleans, text compares without surrounding spaces and without case.
It is strict about what is missing: an output the stage did not write never matches, so a silent
agent takes the default transition, never a branch it did not choose.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from choregos_contracts.workflow import OutputComparison

_TRUE = {"true", "yes"}
_FALSE = {"false", "no"}


def when_holds(when: Mapping[str, Any] | None, outputs: Mapping[str, Any] | None) -> bool:
    """True when every condition of `when` holds on `outputs`; a transition without `when` always holds."""
    if not when:
        return True
    if not outputs:
        return False
    return all(name in outputs and _holds(condition, outputs[name]) for name, condition in when.items())


def _holds(condition: Any, value: Any) -> bool:
    if value is None:
        return False
    if isinstance(condition, OutputComparison):
        return _compare(condition, value)
    if isinstance(condition, Mapping):
        return _compare(OutputComparison.model_validate(condition), value)
    if isinstance(condition, list):
        return any(_equal(choice, value) for choice in condition)
    return _equal(condition, value)


def _compare(comparison: OutputComparison, value: Any) -> bool:
    checks: list[bool] = []
    if comparison.equals is not None:
        checks.append(_equal(comparison.equals, value))
    if comparison.in_ is not None:
        checks.append(any(_equal(choice, value) for choice in comparison.in_))
    if comparison.not_in is not None:
        checks.append(not any(_equal(choice, value) for choice in comparison.not_in))
    bounds = [(comparison.gt, float.__gt__), (comparison.gte, float.__ge__),
              (comparison.lt, float.__lt__), (comparison.lte, float.__le__)]  # fmt: skip
    for bound, operator in bounds:
        if bound is None:
            continue
        number = _number(value)
        checks.append(number is not None and operator(number, float(bound)))
    return all(checks)


def _equal(expected: Any, value: Any) -> bool:
    if isinstance(expected, bool):
        return _boolean(value) is expected
    if isinstance(expected, int | float):
        number = _number(value)
        return number is not None and number == float(expected)
    return (
        isinstance(value, str | int | float)
        and not isinstance(value, bool)
        and (str(value).strip().casefold() == str(expected).strip().casefold())
    )


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None

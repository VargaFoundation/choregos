# SPDX-License-Identifier: Apache-2.0
"""Evaluation of the CEL expressions of an action (contract 03 §2.5), without I/O.

- a **condition** (`expr`, `expect`, `when`) is a boolean CEL expression: anything else, or an
  evaluation error, is an error — never a silent `false` or `true`;
- in a **value** (`with`, `idempotencyKey`), a string of the form `${…}` is a CEL expression;
  any other string is literal.

`facts()` and `window_open()` are not provided by the trial: an expression that calls them fails
to evaluate, which refuses the precondition that uses it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import celpy
from celpy import celtypes
from celpy.adapter import json_to_cel
from celpy.evaluation import CELEvalError

_ENV = celpy.Environment()
_TEMPLATE = re.compile(r"^\$\{(.+)\}$", re.DOTALL)


class CelError(ValueError):
    """An expression that does not compile, does not evaluate, or is not of the expected type."""


def _python(value: Any) -> Any:
    if value is None or isinstance(value, celtypes.BoolType | bool):
        return None if value is None else bool(value)
    if isinstance(value, celtypes.IntType | celtypes.UintType):
        return int(value)
    if isinstance(value, celtypes.DoubleType):
        return float(value)
    if isinstance(value, celtypes.StringType | str):
        return str(value)
    if isinstance(value, celtypes.TimestampType | datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(_python(k)): _python(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_python(v) for v in value]
    return str(value)


def _activation(variables: dict[str, Any]) -> dict[str, Any]:
    activation: dict[str, Any] = {}
    for name, value in variables.items():
        if isinstance(value, datetime):
            activation[name] = celtypes.TimestampType(value)
        else:
            activation[name] = json_to_cel(value)
    return activation


def evaluate(expr: str, variables: dict[str, Any]) -> Any:
    """The value of ``expr``, as plain Python (dict, list, str, int, float, bool, None)."""
    try:
        program = _ENV.program(_ENV.compile(expr))
        result = program.evaluate(_activation(variables))
    except Exception as error:  # celpy raises its own types, and KeyError/TypeError underneath
        raise CelError(f"{expr!r}: {error}") from error
    if isinstance(result, CELEvalError):
        raise CelError(f"{expr!r}: {result}")
    return _python(result)


def condition(expr: str, variables: dict[str, Any]) -> bool:
    result = evaluate(expr, variables)
    if not isinstance(result, bool):
        raise CelError(f"{expr!r} is not a condition: it gives {result!r}")
    return result


def render(value: Any, variables: dict[str, Any]) -> Any:
    """``value`` with every ``${…}`` string replaced by the value of its expression, recursively."""
    if isinstance(value, str):
        match = _TEMPLATE.match(value)
        return evaluate(match.group(1), variables) if match else value
    if isinstance(value, dict):
        return {key: render(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, variables) for item in value]
    return value

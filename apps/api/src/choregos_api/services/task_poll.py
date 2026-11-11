# SPDX-License-Identifier: Apache-2.0
"""What a `poll` trigger reads of an operation's result (ADR 0137 point 2, S63-13): JSON pointers, simple
conditions, and the keys of the objects a run is started for. Pure: no database, no clock.

When `idempotency` runs through a `*` (`/events/*/id`) each item of that array is an OBJECT: `when` is tested
on each one (a pointer that starts at the same array is read relative to the object, any other against the
whole result) and a run is started per object that meets it, under the object's own key. Without a `*`
the result is one object.

A pointer is RFC 6901 with one extension, a `*` segment that stands for every item of an array (or every
value of an object): it resolves to a LIST of values. A condition holds when ANY of the values it
resolves to satisfies it, and `when` holds when EVERY pointer's condition holds. The conditions are
`not_empty`, `abs_gte`, `gte`, `lte` and `eq`; a value that is not a number never satisfies a numeric
one, and an unknown condition never holds (a typo must not start runs).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_EMPTY: tuple[Any, ...] = (None, "", [], {})


def _segments(pointer: str) -> list[str]:
    if pointer in ("", "/"):
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"a JSON pointer starts with `/`: {pointer!r}")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def resolve(document: Any, pointer: str) -> list[Any]:
    """Every value `pointer` designates in `document`; none when a segment does not exist."""
    found = [document]
    for segment in _segments(pointer):
        step: list[Any] = []
        for value in found:
            if segment == "*":
                if isinstance(value, list):
                    step.extend(value)
                elif isinstance(value, dict):
                    step.extend(value.values())
            elif isinstance(value, dict) and segment in value:
                step.append(value[segment])
            elif isinstance(value, list) and segment.isdigit() and int(segment) < len(value):
                step.append(value[int(segment)])
        found = step
    return found


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _holds(operator: str, expected: Any, value: Any) -> bool:
    if operator == "eq":
        return bool(value == expected)
    number, bound = _number(value), _number(expected)
    if number is None or bound is None:
        return False
    if operator == "abs_gte":
        return abs(number) >= bound
    if operator == "gte":
        return number >= bound
    if operator == "lte":
        return number <= bound
    return False


def condition_holds(condition: Any, values: list[Any]) -> bool:
    """`condition` is `{operator: expected}` (every operator must hold); `not_empty` may also be written
    as the bare word `not_empty`."""
    if condition == "not_empty":
        condition = {"not_empty": True}
    if not isinstance(condition, dict) or not condition:
        return False
    for operator, expected in condition.items():
        if operator == "not_empty":
            if any(v not in _EMPTY for v in values) != bool(expected):
                return False
        elif not any(_holds(str(operator), expected, value) for value in values):
            return False
    return True


def when_holds(when: dict[str, Any], result: Any) -> bool:
    """Every pointer of `when` meets its condition in `result`."""
    return bool(when) and all(condition_holds(c, resolve(result, p)) for p, c in when.items())


def object_keys(idempotency: str | None, result: Any) -> list[str]:
    """The identity of each object the result carries, in order and without repeats. Without an
    `idempotency` pointer the result is ONE object, identified by its digest (the same payload twice is
    one object); a pointer that resolves to nothing carries none."""
    if not idempotency:
        canonical = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return ["sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:32]]
    keys = [v if isinstance(v, str) else json.dumps(v, sort_keys=True) for v in resolve(result, idempotency)]
    return list(dict.fromkeys(k for k in keys if k))


def _met(when: dict[str, Any], member: Any, members: str, result: Any) -> bool:
    for pointer, condition in when.items():
        if pointer == members or pointer.startswith(members + "/"):
            values = resolve(member, pointer[len(members) :])
        else:
            values = resolve(result, pointer)
        if not condition_holds(condition, values):
            return False
    return bool(when)


def due_objects(when: dict[str, Any], idempotency: str | None, result: Any) -> list[str]:
    """The keys of the objects of `result` that meet `when`, in order and without repeats: what a poll
    starts a run for (before the keys it already saw are taken out)."""
    segments = _segments(idempotency) if idempotency else []
    if "*" not in segments:
        return object_keys(idempotency, result) if when_holds(when, result) else []
    cut = segments.index("*")
    members = "/" + "/".join([*segments[:cut], "*"])
    rest = "/" + "/".join(segments[cut + 1 :]) if segments[cut + 1 :] else None
    keys: list[str] = []
    for member in resolve(result, members):
        if _met(when, member, members, result):
            keys.extend(object_keys(rest, member))
    return list(dict.fromkeys(keys))

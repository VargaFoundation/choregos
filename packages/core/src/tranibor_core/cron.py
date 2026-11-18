# SPDX-License-Identifier: Apache-2.0
"""A five-field cron with a time zone (S39-19): parse, match, next fire, minimum gap.

Pure and dependency-free (`zoneinfo` only). Fields: minute, hour, day of month, month, day of week.
Each accepts `*`, a number, a range `a-b`, a list `a,b`, a step `*/n` or `a-b/n`; months and days
accept their three-letter names; day of week 0 and 7 are both Sunday. As in classic cron, when both
day of month and day of week are restricted a day matches if EITHER does.

Time zones: the expression is read in local time. A local time that does not exist (the hour
skipped at the spring DST change) never fires; one that exists twice (the autumn change) fires
once, the first time it happens.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

_MONTHS = {
    n: i + 1
    for i, n in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
_DAYS = {n: i for i, n in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
SEARCH_DAYS = 366


@dataclass(frozen=True)
class CronSpec:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday .. 6 = Saturday
    days_restricted: bool
    weekdays_restricted: bool


def _value(token: str, names: dict[str, int], low: int, high: int) -> int:
    number = names[token.lower()] if token.lower() in names else int(token)
    if not low <= number <= high:
        raise ValueError(f"{token!r} is outside {low}-{high}")
    return number


def _field(text: str, low: int, high: int, names: dict[str, int] | None = None) -> frozenset[int]:
    names = names or {}
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise ValueError("empty list item")
        body, _, step_text = part.partition("/")
        step = 1
        if "/" in part:
            step = int(step_text)
            if step < 1:
                raise ValueError("a step is at least 1")
        if body == "*":
            first, last = low, high
        elif "-" in body:
            a, _, b = body.partition("-")
            first, last = _value(a, names, low, high), _value(b, names, low, high)
            if first > last:
                raise ValueError(f"descending range {body!r}")
        else:
            first = _value(body, names, low, high)
            last = high if "/" in part else first
        values.update(range(first, last + 1, step))
    return frozenset(values)


def parse(expression: str) -> CronSpec:
    """Raises `ValueError` with the reason when the expression is not a valid five-field cron."""
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"a cron has 5 fields (minute hour day month weekday), not {len(fields)}")
    try:
        minutes = _field(fields[0], 0, 59)
        hours = _field(fields[1], 0, 23)
        days = _field(fields[2], 1, 31)
        months = _field(fields[3], 1, 12, _MONTHS)
        weekdays = {d % 7 for d in _field(fields[4], 0, 7, _DAYS)}
    except (ValueError, KeyError) as error:
        raise ValueError(f"invalid cron {expression!r}: {error}") from error
    return CronSpec(
        minutes,
        hours,
        days,
        months,
        frozenset(weekdays),
        days_restricted=not fields[2].startswith("*"),
        weekdays_restricted=not fields[4].startswith("*"),
    )


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception as error:  # ZoneInfoNotFoundError, ValueError (bad key)
        raise ValueError(f"unknown time zone {name!r}") from error


def _day_matches(spec: CronSpec, moment: datetime) -> bool:
    if moment.month not in spec.months:
        return False
    day_ok = moment.day in spec.days
    weekday_ok = (moment.weekday() + 1) % 7 in spec.weekdays
    if spec.days_restricted and spec.weekdays_restricted:
        return day_ok or weekday_ok
    return day_ok and weekday_ok


def matches(spec: CronSpec, moment: datetime) -> bool:
    """Whether `moment` (read as its own wall clock) is a fire minute of `spec`."""
    return moment.minute in spec.minutes and moment.hour in spec.hours and _day_matches(spec, moment)


def next_fire(expression: str, after: datetime, tz: str = "UTC") -> datetime:
    """The first fire strictly after `after`, in UTC. `ValueError` if none in a year."""
    spec, zone = parse(expression), _timezone(tz)
    local = after.astimezone(zone).replace(second=0, microsecond=0, tzinfo=None) + timedelta(minutes=1)
    limit = local + timedelta(days=SEARCH_DAYS)
    while local < limit:
        if not _day_matches(spec, local):  # skip the whole day
            local = local.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if local.hour not in spec.hours:  # skip the whole hour
            local = local.replace(minute=0) + timedelta(hours=1)
            continue
        if local.minute in spec.minutes:
            # fold=0 picks the first of an ambiguous time; a non-existent time does not round-trip.
            utc = local.replace(tzinfo=zone, fold=0).astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None) == local and utc > after:
                return utc
        local += timedelta(minutes=1)
    raise ValueError(f"cron {expression!r} never fires within {SEARCH_DAYS} days")


def min_gap(expression: str, tz: str = "UTC", samples: int = 50) -> int:
    """The shortest gap, in seconds, between two of the next `samples` fires."""
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    fires = []
    for _ in range(samples):
        moment = next_fire(expression, moment, tz)
        fires.append(moment)
    if len(fires) < 2:
        raise ValueError("at least two samples are needed")
    return int(min((b - a).total_seconds() for a, b in itertools.pairwise(fires)))

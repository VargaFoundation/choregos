"""The cron of a knowledge source's refresh (S39-19): five fields, a time zone, a minimum gap."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from choregos_core.cron import matches, min_gap, next_fire, parse


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_weekday_morning_in_paris_skips_the_weekend() -> None:
    # Friday 2026-10-16 07:00Z is past 06:00 Paris (CEST, UTC+2): the next fire is Monday.
    assert next_fire("0 6 * * 1-5", utc(2026, 10, 16, 7), "Europe/Paris") == utc(2026, 10, 19, 4)


def test_a_fire_is_strictly_after_the_moment() -> None:
    assert next_fire("0 6 * * *", utc(2026, 10, 16, 6), "UTC") == utc(2026, 10, 17, 6)
    assert next_fire("0 6 * * *", utc(2026, 10, 16, 5, 59, 30), "UTC") == utc(2026, 10, 16, 6)


def test_across_the_autumn_dst_change_a_nightly_fire_happens_once() -> None:
    # 2026-10-25 03:00 -> 02:00 in Paris: 02:30 local exists twice, and fires once (the first).
    first = next_fire("30 2 * * *", utc(2026, 10, 24, 12), "Europe/Paris")
    assert first == utc(2026, 10, 25, 0, 30)
    assert next_fire("30 2 * * *", first, "Europe/Paris") == utc(2026, 10, 26, 1, 30)


def test_a_local_time_skipped_by_the_spring_change_does_not_fire_that_day() -> None:
    # 2026-03-29 02:00 -> 03:00 in Paris: 02:30 does not exist.
    assert next_fire("30 2 * * *", utc(2026, 3, 28, 12), "Europe/Paris") == utc(2026, 3, 30, 0, 30)


def test_steps_names_and_lists_parse() -> None:
    assert parse("*/15 * * * *").minutes == frozenset({0, 15, 30, 45})
    spec = parse("0 6 * jan,feb mon-fri")
    assert spec.months == frozenset({1, 2}) and spec.weekdays == frozenset({1, 2, 3, 4, 5})
    assert parse("0 0 * * 7").weekdays == parse("0 0 * * 0").weekdays == frozenset({0})
    assert parse("10-20/5 * * * *").minutes == frozenset({10, 15, 20})
    assert matches(parse("0 6 * * *"), datetime(2026, 10, 16, 6, 0))
    assert not matches(parse("0 6 * * *"), datetime(2026, 10, 16, 6, 1))


def test_day_of_month_or_weekday_when_both_are_restricted() -> None:
    spec = parse("0 0 13 * fri")
    assert matches(spec, datetime(2026, 11, 13))  # a Friday the 13th
    assert matches(spec, datetime(2026, 11, 20))  # a Friday
    assert matches(spec, datetime(2026, 12, 13))  # a Sunday the 13th


@pytest.mark.parametrize(
    "bad", ["* * * *", "61 * * * *", "* 24 * * *", "a b c d e", "*/0 * * * *", "5-1 * * * *", ""]
)
def test_a_bad_expression_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        parse(bad)


def test_an_unknown_time_zone_is_refused() -> None:
    with pytest.raises(ValueError, match="time zone"):
        next_fire("0 6 * * *", utc(2026, 1, 1), "Mars/Olympus")


def test_a_cron_that_never_fires_is_refused() -> None:
    with pytest.raises(ValueError, match="never fires"):
        next_fire("0 0 31 2 *", utc(2026, 1, 1))


def test_the_minimum_gap() -> None:
    assert min_gap("*/2 * * * *") == 120
    assert min_gap("0 6 * * *") == 86400
    assert min_gap("0,3 6 * * *") == 180
    assert min_gap("0 0 1 1 *", samples=3) >= 365 * 86400

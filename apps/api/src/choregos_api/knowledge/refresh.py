# SPDX-License-Identifier: Apache-2.0
"""When a knowledge source syncs (S39-19, ADR 0051): the row decides, the loop is only a timer.

The `KnowledgeSync` workflow wakes up, asks the activity to run a pass and sleeps for the
`interval_seconds` the activity answers. The activity calls `decide_pass`: is this pass due, and how
long until the next one? That puts interval, cron and manual modes — and the "next sync" the console
shows — in the source row, with no change to the workflow (and so no replay risk).

Pure functions: no I/O, the moment is passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from choregos_core import utcnow
from choregos_core.cron import min_gap, next_fire

MIN_SLEEP_S = 300
MANUAL_IDLE_S = 86400
MIN_CRON_GAP_S = 300
MODES = ("interval", "cron", "manual")

__all__ = [
    "MANUAL_IDLE_S",
    "MIN_CRON_GAP_S",
    "MIN_SLEEP_S",
    "MODES",
    "PassDecision",
    "check_cron",
    "decide_pass",
    "next_sync_after",
    "sleep_until",
    "utcnow",
]


@dataclass(frozen=True)
class PassDecision:
    sync: bool
    sleep_s: int


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _aware(moment: datetime | None) -> datetime | None:
    return None if moment is None else _utc(moment)


def check_cron(expression: str, tz: str) -> None:
    """Raises `ValueError` (with the reason) unless this is a cron the platform accepts."""
    gap = min_gap(expression, tz)  # parses, checks the zone, and that it fires
    if gap < MIN_CRON_GAP_S:
        raise ValueError(f"`{expression}` fires every {gap // 60} min: the minimum is {MIN_CRON_GAP_S // 60}")


def next_sync_after(source: Any, after: datetime) -> datetime | None:
    """When the source syncs next if it synced at `after`; `None` in manual mode."""
    mode = getattr(source, "refresh_mode", "interval")
    if mode == "manual":
        return None
    if mode == "cron":
        return next_fire(str(source.refresh_cron), after, source.refresh_timezone or "UTC")
    return after + timedelta(minutes=int(source.interval_minutes))


def sleep_until(moment: datetime | None, now: datetime) -> int:
    """The loop's sleep: until `moment`, never under `MIN_SLEEP_S`; a day when there is none."""
    if moment is None:
        return MANUAL_IDLE_S
    left = (_utc(moment) - _utc(now)).total_seconds()
    return max(MIN_SLEEP_S, int(left))


def decide_pass(source: Any, now: datetime) -> PassDecision:
    """Whether this pass syncs, and (when it does not) how long the loop sleeps until the next.

    A sync someone asked for (`sync_requested_at` after the last sync) always runs, even in manual
    mode. Otherwise a manual source never syncs; an interval or cron source syncs when it never has
    or its `next_sync_at` has come. A pass that is not due touches nothing: the first pass after
    `continue_as_new` or a worker restart must not sync off-schedule.
    """
    now = _utc(now)
    last, requested = _aware(source.last_synced_at), _aware(source.sync_requested_at)
    nxt = _aware(source.next_sync_at)
    if requested is not None and (last is None or requested > last):
        return PassDecision(True, 0)
    if source.refresh_mode == "manual":
        return PassDecision(False, MANUAL_IDLE_S)
    if last is None or nxt is None or nxt <= now:
        return PassDecision(True, 0)
    return PassDecision(False, sleep_until(nxt, now))

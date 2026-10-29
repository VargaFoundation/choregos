# SPDX-License-Identifier: Apache-2.0
"""One definition of the cross-backend review, shared by the metric and by the reviewer swap.

The policy `review.cross_backend` swaps the reviewer of a coding agent when it is the backend that
wrote the code (orchestrator, `_reviewer_backend`). The metric
`GET /projects/{id}/metrics/cross-backend` asks whether that policy pays. Both must mean the same
thing by "a cross review", by "the implementer" and by the reviewer candidates, so each is defined
here once, as a pure function over plain values: no database, no clock, no network.

Timestamps (`StageRun.created_at`) are compared with each other: they must all be timezone-aware or
all naive, as the rows of one database column are.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from choregos_contracts import StageRole

from .models import KNOWN_BACKEND_NAMES

#: A comparison needs at least this many reviews on each side (same backend, other backend).
MIN_REVIEWS_PER_ARM = 5

#: Two rounded catch rates closer than this are "no clear difference".
_CLEAR_DIFFERENCE = 0.05


@dataclass(frozen=True)
class StageRun:
    """What the cross-backend review needs to know about one run of a work item."""

    run_id: str
    work_item_id: str
    role: str
    backend: str | None
    created_at: datetime
    #: For a review: it found something (a finding was filed, or its result is not `done`).
    caught: bool = False


@dataclass
class Arm:
    """The reviews made by the same backend as the implementer, or by another one."""

    reviews: int = 0
    caught: int = 0
    backends: set[str] = field(default_factory=set)

    @property
    def catch_rate(self) -> float | None:
        """The share of reviews that found something, rounded to 4 decimals (`None` without a review)."""
        return round(self.caught / self.reviews, 4) if self.reviews else None


def counts_as_cross_review(role: str, backend: str | None) -> bool:
    """Whether a run is a review by a coding agent, the only kind the swap can change.

    A verify run is not a review, and a fixed-runtime or remote reviewer (`code`, `prompt`,
    `hosted`, `a2a`) runs no coding-agent backend: the policy never swaps it, so the metric does
    not count it.
    """
    return role == StageRole.REVIEW.value and backend in KNOWN_BACKEND_NAMES


def _implement_runs(runs: Iterable[StageRun]) -> dict[str, list[StageRun]]:
    """The implement runs that name a backend, by work item, oldest first (a tie keeps input order)."""
    by_item: dict[str, list[StageRun]] = {}
    for run in runs:
        if run.role == StageRole.IMPLEMENT.value and run.backend:
            by_item.setdefault(run.work_item_id, []).append(run)
    for history in by_item.values():
        history.sort(key=lambda run: run.created_at)
    return by_item


def _latest_backend(history: Sequence[StageRun], at: datetime | None) -> str | None:
    for run in reversed(history):
        if at is None or run.created_at <= at:
            return run.backend
    return None


def implementer_backend(
    runs: Iterable[StageRun], work_item_id: str, *, at: datetime | None = None
) -> str | None:
    """The backend that last implemented the work item, as of `at` (now, when `at` is not given).

    The latest IMPLEMENT run of the item that names a backend and was created at or before `at`.
    """
    own = (run for run in runs if run.work_item_id == work_item_id)
    return _latest_backend(_implement_runs(own).get(work_item_id, []), at)


def reviewer_candidates(implementer: str, allowed: Sequence[str]) -> list[str]:
    """The backends that may review, in order of preference: the allowed ones (all the known ones
    when the project names none) without the implementer."""
    return [name for name in (allowed or KNOWN_BACKEND_NAMES) if name != implementer]


def cross_backend_arms(runs: Sequence[StageRun]) -> tuple[Arm, Arm]:
    """Count the cross reviews of `runs` as `(same, other)` arms.

    `runs` holds the reviews and the implement runs of the work items; the other roles are
    ignored. A review counts when `counts_as_cross_review` says so and the work item had a known
    implementer when the review was made (a later implementer is not the one it reviewed). It
    lands in `same` when its backend is that implementer, in `other` otherwise.
    """
    history = _implement_runs(runs)
    same, other = Arm(), Arm()
    for run in runs:
        if not counts_as_cross_review(run.role, run.backend) or run.backend is None:
            continue
        implementer = _latest_backend(history.get(run.work_item_id, []), run.created_at)
        if implementer is None:
            continue  # without a known implementer there is nothing to compare with
        arm = same if run.backend == implementer else other
        arm.reviews += 1
        arm.backends.add(run.backend)
        if run.caught:
            arm.caught += 1
    return same, other


def cross_backend_verdict(same: Arm, other: Arm) -> tuple[str, str]:
    """Does a review by another backend catch more? The verdict and its one-line detail.

    The comparison uses the rounded `catch_rate` of each arm, which is also what is displayed.
    """
    if same.reviews < MIN_REVIEWS_PER_ARM or other.reviews < MIN_REVIEWS_PER_ARM:
        return (
            "not enough data",
            f"{other.reviews} review(s) by another backend, {same.reviews} by the same one: "
            f"{MIN_REVIEWS_PER_ARM} are needed on each side to compare.",
        )
    if same.catch_rate is None or other.catch_rate is None:
        return ("not enough data", "one of the two arms has no measurable rate.")
    delta = other.catch_rate - same.catch_rate
    detail = (
        f"another backend finds something in {other.catch_rate:.0%} of the reviews, "
        f"the same one in {same.catch_rate:.0%} ({delta:+.1%})."
    )
    if delta > _CLEAR_DIFFERENCE:
        return ("cross review catches more", detail)
    if delta < -_CLEAR_DIFFERENCE:
        return ("cross review catches less", detail)
    return ("no clear difference", detail)

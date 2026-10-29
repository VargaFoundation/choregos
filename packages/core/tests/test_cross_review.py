"""One definition of the cross-backend review, shared by the metric and by the reviewer swap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from choregos_core.cross_review import (
    MIN_REVIEWS_PER_ARM,
    Arm,
    StageRun,
    counts_as_cross_review,
    cross_backend_arms,
    cross_backend_verdict,
    implementer_backend,
    reviewer_candidates,
)

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def run(
    run_id: str,
    role: str,
    backend: str | None,
    minutes: int,
    *,
    item: str = "wi-1",
    caught: bool = False,
) -> StageRun:
    return StageRun(
        run_id=run_id, work_item_id=item, role=role, backend=backend, created_at=at(minutes), caught=caught
    )


def test_only_reviews_by_coding_agents_count() -> None:
    assert counts_as_cross_review("review", "codex") is True
    assert counts_as_cross_review("review", "claude-code") is True
    assert counts_as_cross_review("verify", "codex") is False
    assert counts_as_cross_review("implement", "codex") is False
    assert counts_as_cross_review("review", "a2a") is False  # a remote reviewer
    assert counts_as_cross_review("review", "code") is False  # a fixed runtime
    assert counts_as_cross_review("review", "prompt") is False
    assert counts_as_cross_review("review", "hosted") is False
    assert counts_as_cross_review("review", None) is False


def test_the_implementer_is_the_one_before_the_review() -> None:
    runs = [
        run("impl-1", "implement", "claude-code", 1),
        run("review-1", "review", "codex", 2),
        run("impl-2", "implement", "codex", 3),
    ]

    assert implementer_backend(runs, "wi-1", at=at(2)) == "claude-code"
    assert implementer_backend(runs, "wi-1", at=at(3)) == "codex"  # created at the same instant counts
    assert implementer_backend(runs, "wi-1") == "codex"  # no `at`: the latest overall
    assert implementer_backend(runs, "wi-1", at=at(0)) is None  # nobody had implemented yet
    assert implementer_backend(runs, "wi-2") is None  # another work item


def test_the_implementer_ignores_runs_without_a_backend() -> None:
    runs = [run("impl-1", "implement", "goose", 1), run("impl-2", "implement", None, 2)]

    assert implementer_backend(runs, "wi-1") == "goose"


def test_candidates_exclude_the_implementer_in_order() -> None:
    assert reviewer_candidates("claude-code", []) == [
        "codex",
        "gemini-cli",
        "goose",
        "opencode",
        "copilot-cli",
    ]
    assert reviewer_candidates("goose", ["goose", "opencode", "codex"]) == ["opencode", "codex"]
    assert reviewer_candidates("codex", ["codex"]) == []  # a single allowed backend: no other reviewer


def test_cross_review_arms_compare_with_the_implementer_of_the_time() -> None:
    runs = [
        run("impl-1", "implement", "claude-code", 1),
        run("review-1", "review", "codex", 2, caught=True),  # another backend than claude-code
        run("impl-2", "implement", "codex", 3),  # the item is implemented again, by codex
        run("review-2", "review", "codex", 4),  # the same backend as the implementer of that time
    ]

    same, other = cross_backend_arms(runs)

    assert (same.reviews, same.caught, same.backends) == (1, 0, {"codex"})
    assert (other.reviews, other.caught, other.backends) == (1, 1, {"codex"})


def test_arms_leave_out_verify_remote_and_orphan_reviews() -> None:
    runs = [
        run("impl-1", "implement", "claude-code", 1),
        run("review-remote", "review", "a2a", 2),
        run("verify-1", "verify", "codex", 3),
        run("review-fixed", "review", "code", 4),
        run("orphan-review", "review", "goose", 5, item="wi-2"),  # no implementer known
        run("review-before", "review", "goose", 0),  # made before anyone implemented
    ]

    same, other = cross_backend_arms(runs)

    assert (same.reviews, other.reviews) == (0, 0)
    assert same.catch_rate is None and other.catch_rate is None


def test_arms_keep_work_items_apart() -> None:
    runs = [
        run("a-impl", "implement", "codex", 1, item="a"),
        run("b-impl", "implement", "goose", 1, item="b"),
        run("a-review", "review", "codex", 2, item="a", caught=True),
        run("b-review", "review", "codex", 2, item="b"),
    ]

    same, other = cross_backend_arms(runs)

    assert (same.reviews, same.caught) == (1, 1)
    assert (other.reviews, other.caught) == (1, 0)


def test_the_catch_rate_is_rounded_to_four_decimals() -> None:
    assert Arm(reviews=3, caught=1).catch_rate == 0.3333
    assert Arm(reviews=3, caught=2).catch_rate == 0.6667
    assert Arm(reviews=0, caught=0).catch_rate is None


def test_the_verdict_needs_five_per_arm() -> None:
    assert MIN_REVIEWS_PER_ARM == 5

    verdict, detail = cross_backend_verdict(Arm(reviews=4, caught=1), Arm(reviews=9, caught=3))

    assert verdict == "not enough data"
    assert (
        detail == "9 review(s) by another backend, 4 by the same one: 5 are needed on each side to compare."
    )
    assert cross_backend_verdict(Arm(reviews=9, caught=3), Arm(reviews=4, caught=1))[0] == "not enough data"


@pytest.mark.parametrize(
    ("same", "other", "verdict", "detail"),
    [
        # The pair of the story: 3 reviews on one side is not enough.
        (
            Arm(reviews=3, caught=1),
            Arm(reviews=7, caught=2),
            "not enough data",
            "7 review(s) by another backend, 3 by the same one: 5 are needed on each side to compare.",
        ),
        # 11/30 rounds to 0.3667 and 5/12 to 0.4167: the rounded delta is 0.04999999999999999, so there is
        # no clear difference, where the raw rates (delta 0.050000000000000044) would say "catches more".
        (
            Arm(reviews=30, caught=11),
            Arm(reviews=12, caught=5),
            "no clear difference",
            "another backend finds something in 42% of the reviews, the same one in 37% (+5.0%).",
        ),
        (
            Arm(reviews=12, caught=5),
            Arm(reviews=30, caught=11),
            "no clear difference",
            "another backend finds something in 37% of the reviews, the same one in 42% (-5.0%).",
        ),
        (
            Arm(reviews=10, caught=3),
            Arm(reviews=10, caught=5),
            "cross review catches more",
            "another backend finds something in 50% of the reviews, the same one in 30% (+20.0%).",
        ),
        (
            Arm(reviews=10, caught=5),
            Arm(reviews=10, caught=3),
            "cross review catches less",
            "another backend finds something in 30% of the reviews, the same one in 50% (-20.0%).",
        ),
        (
            Arm(reviews=6, caught=0),
            Arm(reviews=6, caught=0),
            "no clear difference",
            "another backend finds something in 0% of the reviews, the same one in 0% (+0.0%).",
        ),
    ],
)
def test_the_verdict_uses_the_rounded_rates(same: Arm, other: Arm, verdict: str, detail: str) -> None:
    """Expected strings are those of the verdict as the API computed it before it moved to the core."""
    assert cross_backend_verdict(same, other) == (verdict, detail)

"""The organisation's policy ceiling (S27-05): a project policy may only tighten it."""

from __future__ import annotations

from choregos_core.policy import PolicyEngine, load_preset
from choregos_core.policy_ceiling import PolicyCeiling, clamp, widenings

CEILING = PolicyCeiling.model_validate(
    {
        "budgets": {
            "ticket_usd": {"M": 20},
            "stage_usd": {"default": {"M": 6}, "implement": {"M": 10}},
            "max_turns": {"default": 60},
            "max_minutes": {"implement": 45},
            "tool_calls_per_run": 50,
            "daily_project_usd": 100,
        },
        "attempts": {"default_max": 2, "by_role": {"implement": 2}},
        "scope": {"max_diff_lines": 2000},
        "findings": {"max_per_run": 3},
    }
)


def test_each_widened_field_is_named() -> None:
    solo = load_preset("solo")  # ticket M 25, stage implement M 12, review M 6, max_turns implement 120…
    found = widenings(solo, CEILING)
    assert "budgets.ticket_usd.M: 25 exceeds the organisation's ceiling of 20" in found
    assert "budgets.stage_usd.implement.M: 12 exceeds the organisation's ceiling of 10" in found
    assert "budgets.stage_usd.default.M: 8 exceeds the organisation's ceiling of 6" in found
    assert "budgets.stage_usd.review.M: 6 exceeds the organisation's ceiling of 6" not in found
    assert "budgets.max_turns.implement: 120 exceeds the organisation's ceiling of 60" in found
    assert "budgets.max_minutes.implement: 90 exceeds the organisation's ceiling of 45" in found
    assert "budgets.tool_calls_per_run: unlimited exceeds the organisation's ceiling of 50" in found
    assert "budgets.daily_project_usd: 150 exceeds the organisation's ceiling of 100" in found
    assert "attempts.by_role.implement: 3 exceeds the organisation's ceiling of 2" in found
    assert "scope.max_diff_lines: 3000 exceeds the organisation's ceiling of 2000" in found
    assert "findings.max_per_run: 5 exceeds the organisation's ceiling of 3" in found


def test_a_policy_within_the_ceiling_widens_nothing() -> None:
    clamped, said = clamp(load_preset("solo"), CEILING)
    assert said, "the preset is above this ceiling"
    assert widenings(clamped, CEILING) == [], "clamped, it is within"
    engine = PolicyEngine(clamped)
    assert engine.budget_ticket("M") == 20 and engine.budget_ticket("L") == 60, "only the capped size moves"
    assert engine.stage_usd("implement", "M") == 10 and engine.stage_usd("implement", "L") == 25
    assert engine.stage_usd("review", "M") == 6 and engine.stage_usd("refine", "M") == 4
    assert engine.max_turns("implement") == 60 and engine.max_turns("triage") == 10
    assert engine.attempts_for("implement") == 2 and engine.max_tool_calls_per_run() == 50


def test_defaults_the_policy_does_not_write_are_compared_too() -> None:
    bare = load_preset("solo").model_copy(deep=True)
    bare.budgets.ticket_usd = {}
    assert "budgets.ticket_usd.M: 25 exceeds the organisation's ceiling of 20" in widenings(bare, CEILING)


def test_no_ceiling_no_limit() -> None:
    assert widenings(load_preset("solo"), None) == []
    assert clamp(load_preset("solo"), None)[1] == []

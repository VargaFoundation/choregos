"""The pure rules of the release channels (S40-02, ADR 0071): no database, no HTTP.

The order in which a revision is chosen for a project is the heart of the feature: each step of the
table below turns red if two steps are swapped. The canary bucket is a hash of the agent and the work
item, so that every stage and every retry of one work item gets one revision.
"""

from __future__ import annotations

import pytest
from choregos_api.db.models import Agent, ProjectAgent
from choregos_api.schemas import AgentOverrides, AgentReleaseCreate, AgentReleasePolicy, AgentSpec
from choregos_api.schemas.agents import AgentBudget, AgentLimits
from choregos_api.services.agent_releases import (
    canary_bucket,
    in_canary,
    kind_for,
    loosens,
    served,
)
from choregos_api.services.agents import effective, elargissements
from pydantic import ValidationError


def test_the_canary_bucket_is_stable_for_one_work_item_and_spreads_the_others() -> None:
    assert canary_bucket("agent-1", "item-9") == canary_bucket("agent-1", "item-9")
    buckets = [canary_bucket("agent-1", f"item-{n}") for n in range(1000)]
    assert all(0 <= b < 100 for b in buckets)
    assert 400 <= sum(1 for b in buckets if b < 50) <= 600, "a 50 % share serves about half the items"
    assert canary_bucket("agent-1", "item-9") != canary_bucket("agent-2", "item-9") or any(
        canary_bucket("agent-1", f"i{n}") != canary_bucket("agent-2", f"i{n}") for n in range(20)
    ), "the agent is part of the hash"


def test_a_share_of_zero_serves_nobody_and_a_hundred_serves_everybody() -> None:
    none = Agent(id="a", candidate_version=2, candidate_percent=0)
    everyone = Agent(id="a", candidate_version=2, candidate_percent=100)
    assert not any(in_canary(none, f"w{n}") for n in range(200))
    assert all(in_canary(everyone, f"w{n}") for n in range(200))


def _agent(**kw: object) -> Agent:
    base: dict[str, object] = {
        "id": "a1",
        "stable_version": 1,
        "candidate_version": None,
        "candidate_percent": 0,
    }
    return Agent(**{**base, **kw})


def _pin(version: int | None = None, channel: str | None = None) -> ProjectAgent:
    return ProjectAgent(version=version, channel=channel)


TESTING = {"candidate_version": 2, "candidate_percent": 100}


@pytest.mark.parametrize(
    ("agent", "pin", "project_channel", "work_item", "expected"),
    [
        # a fixed pin beats everything, even a project that follows testing and a full canary
        (_agent(**TESTING), _pin(version=1), "candidate", "w1", (1, "pin", None)),
        # a channel pin on testing beats the project's switch and the canary
        (_agent(**TESTING), _pin(channel="candidate"), "stable", "w1", (2, "pin_channel", "candidate")),
        # a channel pin on production is not moved by the canary nor by the project's switch
        (_agent(**TESTING), _pin(channel="stable"), "candidate", "w1", (1, "pin_channel", "stable")),
        # testing with no candidate falls back to production
        (_agent(), _pin(channel="candidate"), "stable", "w1", (1, "pin_channel", "stable")),
        # the project follows testing
        (_agent(candidate_version=2), None, "candidate", None, (2, "project_channel", "candidate")),
        # ... but with no candidate it stays on production
        (_agent(), None, "candidate", "w1", (1, "stable", "stable")),
        # a canary share, for a work item inside it
        (_agent(**TESTING), None, "stable", "w1", (2, "canary", "candidate")),
        # no work item: a canary cannot apply (the resolution endpoint has none)
        (_agent(**TESTING), None, "stable", None, (1, "stable", "stable")),
        # a share of zero
        (_agent(candidate_version=2), None, "stable", "w1", (1, "stable", "stable")),
        # production
        (_agent(), None, "stable", "w1", (1, "stable", "stable")),
        # no production pointer: the resolver reads the latest revision
        (_agent(stable_version=None), None, "stable", "w1", (None, "latest", None)),
    ],
)
def test_the_order_in_which_a_revision_is_served(
    agent: Agent,
    pin: ProjectAgent | None,
    project_channel: str,
    work_item: str | None,
    expected: tuple[int | None, str, str | None],
) -> None:
    assert served(agent, pin=pin, project_channel=project_channel, work_item_id=work_item) == expected


def test_the_canary_follows_the_bucket_of_the_work_item() -> None:
    agent = _agent(candidate_version=2, candidate_percent=50)
    inside = next(f"w{n}" for n in range(1000) if canary_bucket("a1", f"w{n}") < 50)
    outside = next(f"w{n}" for n in range(1000) if canary_bucket("a1", f"w{n}") >= 50)
    assert served(agent, pin=None, project_channel="stable", work_item_id=inside)[1] == "canary"
    assert served(agent, pin=None, project_channel="stable", work_item_id=outside)[1] == "stable"


def test_the_kind_of_a_move() -> None:
    assert kind_for("candidate", None, 3, was_in_production=False) == "withdraw"
    assert kind_for("candidate", 4, 3, was_in_production=False) == "promote"
    assert kind_for("stable", 2, 3, was_in_production=True) == "rollback"
    assert kind_for("stable", 2, 3, was_in_production=False) == "promote", (
        "an old revision never in production"
    )
    assert kind_for("stable", 4, 3, was_in_production=False) == "promote"
    assert kind_for("stable", 3, None, was_in_production=False) == "promote"


def test_a_revision_published_to_production_is_never_a_candidate() -> None:
    assert AgentReleasePolicy().on_publish == "stable"
    for gate in (
        {"require_candidate": True},
        {"min_candidate_runs": 1},
        {"min_success_rate": 0.9},
        {"min_soak_hours": 1},
    ):
        with pytest.raises(ValidationError, match="never a candidate"):
            AgentReleasePolicy(on_publish="stable", stable=gate)  # type: ignore[arg-type]
        assert AgentReleasePolicy(on_publish="candidate", stable=gate).on_publish == "candidate"  # type: ignore[arg-type]
        assert AgentReleasePolicy(on_publish="none", stable=gate).on_publish == "none"  # type: ignore[arg-type]


def test_a_share_belongs_to_testing() -> None:
    with pytest.raises(ValidationError, match="candidate"):
        AgentReleaseCreate(channel="stable", version=2, percent=10)
    assert AgentReleaseCreate(channel="candidate", version=2, percent=10).percent == 10


def test_loosening_a_policy() -> None:
    strict = AgentReleasePolicy(
        on_publish="candidate",
        stable={
            "require_candidate": True,
            "min_candidate_runs": 5,
            "min_success_rate": 0.9,
            "min_soak_hours": 24,
        },  # type: ignore[arg-type]
    )
    assert not loosens(strict, strict)
    assert loosens(strict, AgentReleasePolicy(on_publish="stable"))
    assert loosens(
        strict,
        strict.model_copy(update={"stable": strict.stable.model_copy(update={"require_candidate": False})}),
    )
    assert loosens(
        strict,
        strict.model_copy(update={"stable": strict.stable.model_copy(update={"min_candidate_runs": 4})}),
    )
    assert loosens(
        strict,
        strict.model_copy(update={"stable": strict.stable.model_copy(update={"min_success_rate": None})}),
    )
    assert loosens(
        strict, strict.model_copy(update={"stable": strict.stable.model_copy(update={"min_soak_hours": 1})})
    )
    assert loosens(
        strict, strict.model_copy(update={"stable": strict.stable.model_copy(update={"step_up_minutes": 60})})
    )
    tighter = strict.model_copy(
        update={"stable": strict.stable.model_copy(update={"min_candidate_runs": 10})}
    )
    assert not loosens(strict, tighter)


def test_an_override_never_widens_the_revision_it_meets() -> None:
    """A pin that follows a channel meets revisions its override was not validated against: the
    effective value is the minimum, never the override's (red with the old assignment)."""
    spec = AgentSpec(budget=AgentBudget(run_usd=1.0, daily_usd=5.0), limits=AgentLimits(max_turns=10))
    loose = AgentOverrides(budget=AgentBudget(run_usd=2.0, daily_usd=3.0), limits=AgentLimits(max_turns=50))
    result = effective(spec, loose)
    assert result.budget.run_usd == 1.0, "2 $ must not widen v4's 1 $"
    assert result.budget.daily_usd == 3.0, "a tighter override still applies"
    assert result.limits.max_turns == 10
    unbounded = AgentSpec()
    assert effective(unbounded, loose).budget.run_usd == 2.0, "tightening an unbounded cap"
    assert elargissements(spec, loose), "pinning such an override on a fixed revision is still refused"

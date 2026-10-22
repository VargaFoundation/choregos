"""What a web search backend keeps, and whether an organisation accepts it (ADR 0083): an override is
only ever stricter and names its terms; an unknown value fails every bound that reads it; the tighter
of the ceiling and the policy applies; a project cannot widen the ceiling."""

from __future__ import annotations

import pytest
from choregos_contracts import Policy
from choregos_contracts.policy import WebSearchPolicy
from choregos_core import load_preset
from choregos_core.policy_ceiling import CeilingWebSearch, PolicyCeiling, clamp, widenings
from choregos_core.web_search import DataHandling, shortfalls, stricter

KNOWN = DataHandling(
    query_retention_days=30, query_reuse=False, region="us", terms_ref="https://x/terms@2026"
)
TERMS = "contract X-2026, §4"


def _bound(**fields: object) -> CeilingWebSearch:
    return CeilingWebSearch.model_validate({"mode": "allow", **fields})


# ── stricter ──


def test_an_override_may_only_tighten_and_must_name_its_terms() -> None:
    assert stricter(KNOWN, None) == KNOWN
    assert stricter(KNOWN, {"query_retention_days": None}) == KNOWN, "a None changes nothing"
    tighter = stricter(KNOWN, {"query_retention_days": 0, "terms_ref": TERMS})
    assert tighter.query_retention_days == 0 and tighter.terms_ref == TERMS and tighter.region == "us"
    with pytest.raises(ValueError, match="terms_ref"):
        stricter(KNOWN, {"query_retention_days": 0})
    with pytest.raises(ValueError, match="longer than"):
        stricter(KNOWN, {"query_retention_days": 90, "terms_ref": TERMS})
    with pytest.raises(ValueError, match="true is wider"):
        stricter(KNOWN, {"query_reuse": True, "terms_ref": TERMS})
    with pytest.raises(ValueError, match="declares `us`"):
        stricter(KNOWN, {"region": "eu", "terms_ref": TERMS})
    with pytest.raises(ValueError, match="unknown field"):
        stricter(KNOWN, {"retention": 0, "terms_ref": TERMS})
    with pytest.raises(ValueError, match="0 or more"):
        stricter(KNOWN, {"query_retention_days": -1, "terms_ref": TERMS})


def test_an_unknown_declaration_can_be_stated_by_the_organisation() -> None:
    stated = stricter(
        DataHandling(), {"query_retention_days": 7, "query_reuse": True, "region": "eu", "terms_ref": TERMS}
    )
    assert stated == DataHandling(7, True, "eu", TERMS)


# ── shortfalls ──


def test_no_section_or_deny_offers_nothing() -> None:
    zero = DataHandling(0, False, "eu", TERMS)
    assert shortfalls("brave", zero, None, WebSearchPolicy())
    assert shortfalls("brave", zero, CeilingWebSearch(), WebSearchPolicy()), "the ceiling is born closed"
    assert shortfalls("brave", zero, _bound(), WebSearchPolicy(mode="deny"))
    assert shortfalls("brave", zero, _bound(), WebSearchPolicy()) == []


def test_unknown_fails_every_bound_that_reads_it() -> None:
    unknown = DataHandling()
    said = shortfalls("brave", unknown, _bound(max_query_retention_days=0, regions=["eu"]), WebSearchPolicy())
    assert len(said) == 3, said  # retention, reuse, region
    assert (
        shortfalls("brave", unknown, _bound(allow_query_reuse=True), WebSearchPolicy(allow_query_reuse=True))
        == []
    )


def test_the_tighter_of_ceiling_and_policy_applies() -> None:
    week = DataHandling(7, False, None, TERMS)
    assert shortfalls("brave", week, _bound(max_query_retention_days=30), WebSearchPolicy()) == []
    assert shortfalls(
        "brave", week, _bound(max_query_retention_days=30), WebSearchPolicy(max_query_retention_days=1)
    )
    reuses = DataHandling(0, True, None, TERMS)
    assert shortfalls("brave", reuses, _bound(allow_query_reuse=True), WebSearchPolicy()), (
        "the policy says no"
    )
    assert shortfalls("brave", reuses, _bound(), WebSearchPolicy(allow_query_reuse=True)), (
        "the ceiling says no"
    )
    assert shortfalls("brave", week, _bound(backends=["tavily"]), WebSearchPolicy())


# ── the ceiling against the project ──


def _policy(**fields: object) -> Policy:
    policy = load_preset("solo").model_copy(deep=True)
    for key, value in fields.items():
        setattr(policy.web_search, key, value)
    return policy


def test_a_project_cannot_widen_the_ceiling_and_clamp_brings_it_down() -> None:
    ceiling = PolicyCeiling.model_validate({"web_search": {"mode": "allow", "max_query_retention_days": 7}})
    wide = _policy(max_query_retention_days=30, allow_query_reuse=True)
    said = widenings(wide, ceiling)
    assert [s.split(":")[0] for s in said] == [
        "web_search.max_query_retention_days",
        "web_search.allow_query_reuse",
    ]
    clamped, _ = clamp(wide, ceiling)
    assert clamped.web_search.max_query_retention_days == 7 and clamped.web_search.allow_query_reuse is False
    assert widenings(_policy(max_query_retention_days=1), ceiling) == []


def test_the_default_policy_widens_nothing_even_under_deny() -> None:
    """A project that never mentions web search must still save under a closed ceiling."""
    assert widenings(_policy(), PolicyCeiling.model_validate({"web_search": {"mode": "deny"}})) == []
    assert widenings(_policy(max_query_retention_days=99), PolicyCeiling()) == [], "no section, nothing read"


def test_the_regulated_preset_keeps_nothing() -> None:
    regulated = load_preset("regulated").web_search
    assert regulated.max_query_retention_days == 0 and regulated.allow_query_reuse is False

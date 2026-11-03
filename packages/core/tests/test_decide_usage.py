# SPDX-License-Identifier: Apache-2.0
"""The `decide` usage (ADR 0121 §3): only a catalogue entry that enables it serves a decision step."""

from __future__ import annotations

from choregos_core.models import USAGES, CatalogueEntry, ModelPolicy


def test_decide_is_a_usage_beside_route() -> None:
    assert "decide" in USAGES
    assert "route" in USAGES


def test_a_decision_model_needs_an_entry_even_in_open_mode() -> None:
    """`check` lets an unknown model through in `open` mode; a decision step must not."""
    policy = ModelPolicy(mode="open")
    assert policy.check("platform/standard", usage="decide") is None
    refusal = policy.check_decision("platform/standard")
    assert refusal is not None and "no entry" in refusal and "decide" in refusal


def test_an_entry_serves_a_decision_only_with_the_decide_usage() -> None:
    policy = ModelPolicy(
        entries={
            "platform/decide": CatalogueEntry(usages=("decide",)),
            "platform/standard": CatalogueEntry(usages=("agent",)),
            "platform/off": CatalogueEntry(enabled=False, usages=("decide",)),
        }
    )
    assert policy.check_decision("platform/decide") is None
    chat = policy.check_decision("platform/standard")
    assert chat is not None and "`decide`" in chat
    off = policy.check_decision("platform/off")
    assert off is not None and "disabled" in off


def test_a_decision_model_is_not_an_agent_s_model() -> None:
    policy = ModelPolicy(entries={"platform/decide": CatalogueEntry(usages=("decide",))})
    refusal = policy.check("platform/decide", usage="agent")
    assert refusal is not None and "`agent`" in refusal

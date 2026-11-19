# SPDX-License-Identifier: Apache-2.0
"""The palette's fuzzy match (S67-24)."""

from __future__ import annotations

from tranibor_cli import fuzzy


def test_an_empty_query_matches_everything_and_keeps_the_order() -> None:
    assert fuzzy.match("", "anything") == (0, [])
    assert [t for t, _ in fuzzy.rank("", ["b", "a", "c"], str)] == ["b", "a", "c"]


def test_the_characters_must_come_in_order_and_all_be_there() -> None:
    assert fuzzy.match("fnrd", "FIN-12 Fix the totals rounding") is not None
    assert fuzzy.match("dnrf", "FIN-12 Fix the totals rounding") is None
    assert fuzzy.match("xyz", "Billing API") is None
    assert fuzzy.match("billingg", "Billing API") is None  # a character is used once


def test_case_and_spaces_in_the_query_are_ignored() -> None:
    assert fuzzy.match("BILL API", "Billing API") is not None
    assert fuzzy.match("billing api", "BILLING API") is not None


def test_the_positions_are_the_ones_to_highlight() -> None:
    found = fuzzy.match("ba", "Billing API")
    assert found is not None
    assert found[1] == [0, 8]  # the word starts, not the first `a` found


def test_a_prefix_beats_a_scattered_match_and_a_word_start_beats_the_middle() -> None:
    texts = ["Open a conversation", "Conversations", "New conversation"]
    assert next(t for t, _ in fuzzy.rank("conv", texts, str)) == "Conversations"
    ranked = [t for t, _ in fuzzy.rank("api", ["Rapid", "Billing API"], str)]
    assert ranked == ["Billing API", "Rapid"]


def test_consecutive_characters_beat_spread_ones() -> None:
    ranked = [t for t, _ in fuzzy.rank("tot", ["the old tower", "totals"], str)]
    assert ranked == ["totals", "the old tower"]


def test_equal_scores_keep_the_given_order() -> None:
    assert [t for t, _ in fuzzy.rank("a", ["a1", "a2", "a3"], str)] == ["a1", "a2", "a3"]


def test_rank_leaves_out_what_does_not_match() -> None:
    assert [t for t, _ in fuzzy.rank("fin", ["Finance", "Legal", "Platform"], str)] == ["Finance"]

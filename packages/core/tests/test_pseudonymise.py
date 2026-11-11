# SPDX-License-Identifier: Apache-2.0
"""The pseudonymiser of candidate eval cases (ADR 0140, S63-17)."""

from __future__ import annotations

from choregos_core.pseudonymise import Pseudonymiser


def test_emails_and_upns_become_stable_placeholders() -> None:
    p = Pseudonymiser()
    text = "Write to ada@corp.example or ADA@corp.example, cc grace.hopper@corp.example."
    assert p.text(text) == "Write to <email_1> or <email_1>, cc <email_2>."
    assert p.text("again grace.hopper@corp.example") == "again <email_2>", "stable across calls"


def test_given_names_are_replaced_whole_word_and_case_insensitively() -> None:
    p = Pseudonymiser(["Ada Lovelace", "Ada", "Bo"])
    out = p.text("ada lovelace met Ada and Bob; Bo agreed.")
    assert out == "<person_1> met <person_2> and Bob; <person_3> agreed."
    assert "Bob" in out, "a longer word is not a name"


def test_ibans_and_long_numbers_are_replaced_but_not_short_figures() -> None:
    p = Pseudonymiser()
    out = p.text(
        "IBAN FR76 3000 6000 0112 3456 7890 189, call +33 6 12 34 56 78. The price is 12.34 EUR, 2026."
    )
    assert "FR76" not in out and "6 12 34" not in out
    assert "<iban_1>" in out and "<number_1>" in out
    assert "12.34 EUR, 2026" in out


def test_values_are_walked_through_nested_json() -> None:
    p = Pseudonymiser(["Ada"])
    value = {"turns": ["I am Ada", "mail ada@x.io"], "n": 3, "nested": {"who": ["Ada"]}}
    assert p.value(value) == {
        "turns": ["I am <person_1>", "mail <email_1>"],
        "n": 3,
        "nested": {"who": ["<person_1>"]},
    }

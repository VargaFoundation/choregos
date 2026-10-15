"""The contrast budget of a served theme: the console's rules (ADR 0043), computed in the API."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from choregos_api.theme import (
    DEFAULT_THEME,
    MODES,
    budget_violations,
    contrast_ratio,
    effective_tokens,
    load_defaults,
    themable_roles,
    validate_theme,
)

from .theme_fixtures import SAMPLE_THEME, sample


def _messages(theme: dict[str, Any]) -> str:
    return " | ".join(str(v) for v in validate_theme(theme))


def _with(mode: str, **tokens: str) -> dict[str, Any]:
    theme = copy.deepcopy(DEFAULT_THEME)
    theme["tokens"][mode] = tokens
    return theme


def test_the_defaults_have_52_roles_and_pass_their_own_budget() -> None:
    assert len(themable_roles()) == 52
    for mode in MODES:
        assert budget_violations(effective_tokens(DEFAULT_THEME, mode), mode) == []
    assert validate_theme(DEFAULT_THEME) == []


def test_the_defaults_are_the_consoles_not_a_stub() -> None:
    defaults = load_defaults()
    assert defaults["dark"]["surface"] == "#111113"
    assert defaults["light"]["surface"] == "#fcfcfd"


def test_a_neutral_sample_palette_passes_in_both_modes() -> None:
    assert validate_theme(SAMPLE_THEME) == []


def test_ink_that_is_too_hard_is_refused() -> None:
    message = _messages(_with("light", ink="#000000", surface="#ffffff", raised="#ffffff"))
    assert "ink on surface: too hard" in message


def test_ink_that_is_too_soft_is_refused() -> None:
    assert "ink on surface" in _messages(_with("light", ink="#9a9a9a"))


def test_text_on_an_accent_fill_needs_aa() -> None:
    message = _messages(_with("light", accent="#ffcc00", **{"accent-ink": "#ffffff"}))
    assert "text on a solid action" in message


def test_a_status_ink_must_read_on_its_own_soft_fill() -> None:
    assert "status text" in _messages(_with("dark", **{"failed-ink": "#222222"}))


def test_a_control_border_that_vanishes_is_refused() -> None:
    assert "control border" in _messages(_with("light", **{"line-strong": "#f0f0f0"}))


def test_the_sidebar_must_be_darker_than_the_page() -> None:
    assert "sidebar" in _messages(_with("dark", sidebar="#ffffff"))


def test_the_focus_ring_must_be_visible() -> None:
    assert "focus ring" in _messages(_with("light", focus="#fcfcfd"))


def test_a_light_override_does_not_alter_the_dark_mode() -> None:
    theme = _with("light", ink="#000000")
    assert effective_tokens(theme, "dark") == load_defaults()["dark"]
    assert "(light)" in _messages(theme)
    assert "(dark)" not in _messages(theme)


@pytest.mark.parametrize("bad", ["#fff", "red", "var(--x)", "#12345g", "rgb(0,0,0)", "#ffffff80", ""])
def test_a_colour_that_is_not_six_digit_hex_is_refused(bad: str) -> None:
    assert validate_theme(_with("dark", accent=bad)) != []


def test_an_unknown_role_is_refused_by_name() -> None:
    assert "tokens.dark.shadow-raised: unknown colour role" in _messages(
        _with("dark", **{"shadow-raised": "#000000"})
    )


def test_upper_case_hex_is_accepted_and_measured() -> None:
    assert validate_theme(_with("dark", accent="#1F7A5A", **{"accent-hover": "#18654A"})) == []


def test_the_ratio_is_the_wcag_one() -> None:
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21)
    assert contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)


def test_each_override_is_judged_with_the_others_from_the_defaults() -> None:
    """A dark surface with the default (dark) ink fails: the budget sees the effective colours."""
    assert sample() and "ink on surface" in _messages(_with("dark", surface="#ffffff", raised="#ffffff"))

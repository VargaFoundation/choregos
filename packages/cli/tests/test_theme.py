# SPDX-License-Identifier: Apache-2.0
"""Colour orients, and is never alone (ADR 0154 §9): the terminal's statuses and identity hues."""

from __future__ import annotations

import io

import pytest
from choregos_cli import theme
from rich.console import Console

#: `workflowHue(name)` as the console computes it (`apps/web/src/lib/workflow-hue.ts`), run by Node on
#: 2026-11-12: the same workflow has the same hue on the board and in the terminal.
CONSOLE_HUES = {
    "delivery": 6,
    "Delivery ": 6,
    "incident-response": 2,
    "release-train": 6,
    "🚀 launch": 4,
    "a": 5,
    "document-request": 5,
    "onboarding": 1,
}


@pytest.mark.parametrize(("name", "expected"), sorted(CONSOLE_HUES.items()))
def test_a_name_has_the_console_s_hue(name: str, expected: int) -> None:
    assert theme.hue(name) == expected


def test_no_identity_hue_is_red() -> None:
    assert not [style for style in theme.HUES if "red" in style]
    assert theme.hue_style("onboarding") == theme.HUES[0]


def test_every_status_has_a_glyph_an_ascii_stand_in_and_a_word() -> None:
    for value, status in theme.STATUSES.items():
        assert status.glyph.strip() and status.word.strip(), value
        assert status.ascii.isascii() and status.ascii.strip(), value


def test_an_unknown_status_reads_as_its_own_word() -> None:
    status = theme.status_of("paused_by_policy")
    assert status.word == "paused_by_policy" and status.glyph == "?"
    assert theme.status_of("").word == "unknown"


def _plain(encoding: str) -> Console:
    """A console writing to a terminal of that encoding."""
    return Console(file=io.TextIOWrapper(io.BytesIO(), encoding=encoding), color_system=None)


def test_a_badge_falls_back_to_ascii_where_the_encoding_cannot_draw_the_glyph() -> None:
    assert theme.badge(theme.RUNNING, _plain("utf-8")).plain == "● running"
    assert theme.badge(theme.RUNNING, _plain("ascii")).plain == "* running"
    assert theme.badge(theme.WAITING, _plain("cp1252")).plain == "! waiting for you"


@pytest.mark.parametrize(
    ("environ", "colour"),
    [({}, True), ({"NO_COLOR": "1"}, False), ({"NO_COLOR": ""}, True), ({"TERM": "dumb"}, False)],
)
def test_colour_follows_the_environment(environ: dict[str, str], colour: bool) -> None:
    assert theme.wants_colour(environ) is colour


def test_a_monochrome_console_renders_no_style_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    target = theme.console(file=stream)
    target.print("[bold]bold[/bold]", theme.badge(theme.FAILED, target))
    assert "\x1b[" not in stream.getvalue()
    assert target.color_system is None

"""An agent's author chooses the language its output is written in (ADR 0126).

Absent or `en`, the frame is today's, byte for byte: a version published without a language runs the
prompt it always ran. Any other tag swaps the language line, and only it.
"""

from __future__ import annotations

import pytest
from choregos_playbooks import INVARIANTS, OUTPUT_CONTRACT, frame, invariants_in, language_name

ENGLISH = "Write in English everything a person will read"

#: The frame as it was before `language` existed: the default must not move by a byte.
TODAYS_FRAME = (
    "Prepare the plan.\n\n---\n\n"
    "## What the platform requires (this frame cannot be changed)\n\n"
    f"{INVARIANTS}\n\n{OUTPUT_CONTRACT}\n"
)


@pytest.mark.parametrize("language", [None, "en"])
def test_the_default_frame_is_unchanged_byte_for_byte(language: str | None) -> None:
    assert frame("Prepare the plan.", language) == TODAYS_FRAME
    assert frame("Prepare the plan.") == TODAYS_FRAME
    assert invariants_in(language) is INVARIANTS


@pytest.mark.parametrize(
    ("tag", "name"),
    [("fr", "French"), ("de", "German"), ("pt-BR", "Brazilian Portuguese"), ("ja", "Japanese")],
)
def test_a_language_replaces_the_english_line_and_only_it(tag: str, name: str) -> None:
    framed = frame("Prepare the plan.", tag)
    assert f"Write in {name} everything a person will read" in framed
    assert ENGLISH not in framed, "one language, not two"
    # What a machine reads keeps its language and its shape.
    assert "the enum values of the result contract (`status`, `type`, `severity`)" in framed
    assert "the shape of\n  `.choregos/result.json`" in framed
    assert OUTPUT_CONTRACT in framed
    # Every other invariant is the same.
    others = INVARIANTS.splitlines()[3:]
    assert all(line in framed for line in others)
    assert framed.count("- ") == frame("Prepare the plan.").count("- ")


def test_a_tag_without_a_name_is_named_by_its_tag() -> None:
    assert language_name("fil") == "the language tagged `fil`"
    assert "Write in the language tagged `fil` everything a person will read" in invariants_in("fil")


def test_the_english_line_the_language_replaces_is_in_the_invariants() -> None:
    """The swap is a text replacement: an edit of the English line that forgets it fails here."""
    assert invariants_in("fr") != INVARIANTS
    assert len(invariants_in("fr").splitlines()) == len(INVARIANTS.splitlines()) + 3

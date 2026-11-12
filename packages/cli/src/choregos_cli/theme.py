# SPDX-License-Identifier: Apache-2.0
"""Colour orients, and is never alone (ADR 0154 §9, ADR 0120 carried to the terminal).

Every status is a glyph and a word first, a colour second: a monochrome terminal, a pipe, `NO_COLOR` or
`--no-color` lose nothing. The colours are the terminal's own sixteen, so the person's theme sets the
contrast; no background is painted. An encoding that cannot draw the glyphs gets ASCII ones.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TextIO

from rich.console import Console
from rich.text import Text

#: Set by `choregos --no-color`: every console the commands make after it is monochrome.
MONOCHROME = False


@dataclass(frozen=True)
class Status:
    """How one status reads: a glyph, its ASCII stand-in, a word, a colour."""

    glyph: str
    ascii: str
    word: str
    style: str


RUNNING = Status("●", "*", "running", "blue")
QUEUED = Status("○", "o", "queued", "bright_black")
WAITING = Status("⏸", "!", "waiting for you", "yellow")
DONE = Status("✓", "+", "done", "green")
FAILED = Status("✗", "x", "failed", "red")
STOPPED = Status("■", "#", "stopped", "bright_black")
UNKNOWN = Status("?", "?", "unknown", "default")

#: An entry's `status` (a run's, a session's, a turn's: `ActivityEntry.status`) read as one of the above.
STATUSES: dict[str, Status] = {
    "queued": QUEUED,
    "running": RUNNING,
    "open": RUNNING,
    "succeeded": DONE,
    "completed": DONE,
    "closed": DONE,
    "failed": FAILED,
    "timed_out": Status("✗", "x", "timed out", "red"),
    "cancelled": Status("■", "#", "cancelled", "bright_black"),
    "waiting": WAITING,
}

#: The frames of the workspace's parts (ADR 0154 §2): the live tree in blue, the decisions in amber.
TREE_FRAME = "blue"
DECISIONS_FRAME = "yellow"

#: The console's eight identity hues (ADR 0120 §2: indigo, teal, amber, pink, cyan, grass, orange, violet),
#: as the nearest of the terminal's sixteen colours. None is red: red means a failure.
HUES = ("bright_blue", "cyan", "yellow", "magenta", "bright_cyan", "green", "bright_yellow", "bright_magenta")


def status_of(value: str) -> Status:
    """The status an API value names; an unknown one says so, with the value as its word."""
    found = STATUSES.get(value)
    return (
        found if found is not None else Status(UNKNOWN.glyph, UNKNOWN.ascii, value or UNKNOWN.word, "default")
    )


def _fnv1a(text: str) -> int:
    """The console's `fnv1a` (`apps/web/src/lib/workflow-hue.ts`), over UTF-16 code units as JavaScript
    reads them."""
    value = 0x811C9DC5
    units = text.encode("utf-16-le")
    for index in range(0, len(units), 2):
        value ^= units[index] | (units[index + 1] << 8)
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def hue(name: str) -> int:
    """The hue (1-8) of a workflow or a project: the same number as the console's `workflowHue(name)`."""
    return _fnv1a(name.strip().lower()) % len(HUES) + 1


def hue_style(name: str) -> str:
    return HUES[hue(name) - 1]


def wants_colour(environ: dict[str, str] | None = None) -> bool:
    """`NO_COLOR` (any non-empty value), `TERM=dumb` and `--no-color` turn colour off."""
    env = os.environ if environ is None else environ
    return not (MONOCHROME or env.get("NO_COLOR") or env.get("TERM") == "dumb")


def console(*, stderr: bool = False, file: TextIO | None = None) -> Console:
    """A console that follows the rules above. Without colour, no escape sequence at all: Rich renders no
    style when it has no colour system (`NO_COLOR` alone would still let bold through)."""
    if wants_colour():
        return Console(stderr=stderr, file=file, highlight=False)
    return Console(stderr=stderr, file=file, highlight=False, color_system=None, no_color=True)


def unicode_ok(target: Console) -> bool:
    return target.encoding.lower().replace("-", "").startswith("utf")


def badge(status: Status, target: Console) -> Text:
    """`● running`: the glyph (ASCII when the encoding cannot draw it) and the word, in the status's
    colour."""
    glyph = status.glyph if unicode_ok(target) else status.ascii
    return Text(f"{glyph} {status.word}", style=status.style)

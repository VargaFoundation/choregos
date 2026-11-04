"""Does `AGENTS.md` still say something, and does it say it true?

On 2026-09-24, switching the documentation to English replaced the content of `AGENTS.md` with
`@AGENTS.md`: an import of itself, eleven bytes. `CLAUDE.md` being a symbolic link to this file,
the repository's instructions were empty on both sides, and nothing saw it: not the CI, not the
lint, not a test. The README kept saying "read AGENTS.md".

Two properties, and the second is the real one:

  * the file has substance and does not reference itself;
  * **every `make <target>` it names really exists in the Makefile.** The 2026-09-24 review
    already blamed this file for wrong commands: an instruction that sends an agent to a missing
    target costs more than no instruction at all.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"
MAKEFILE = ROOT / "Makefile"

#: Sections without which the file no longer does the job expected of it.
SECTIONS = ("## Context", "## Languages", "## Commands", "## Conventions", "## Definition of done")


def _text() -> str:
    return AGENTS.read_text(encoding="utf-8")


def test_the_instructions_do_not_reference_themselves() -> None:
    text = _text().strip()
    assert text != "@AGENTS.md", (
        "AGENTS.md became an import of itself again. `CLAUDE.md` points at it: the "
        "repository's instructions are empty on both sides."
    )
    assert len(text) > 1000, f"AGENTS.md is only {len(text)} bytes: it says nothing any more"


def test_the_expected_sections_are_there() -> None:
    text = _text()
    missing = [title for title in SECTIONS if title not in text]
    assert not missing, f"sections missing from AGENTS.md: {missing}"


def test_claude_md_and_agents_md_are_the_same_file() -> None:
    """Two instruction files that diverge are a wrong instruction file."""
    claude = ROOT / "CLAUDE.md"
    assert claude.exists(), "CLAUDE.md is gone"
    assert claude.resolve() == AGENTS.resolve(), (
        "CLAUDE.md is no longer the same file as AGENTS.md: they will diverge"
    )


def test_every_make_target_cited_exists() -> None:
    targets = {
        line.split(":", 1)[0]
        for line in MAKEFILE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^[a-z][a-z0-9-]*:", line)
    }
    cited = set(re.findall(r"`make ([a-z][a-z0-9-]*)`", _text()))
    assert cited, "AGENTS.md no longer cites any `make` command"
    unknown = sorted(cited - targets)
    assert not unknown, (
        f"AGENTS.md sends readers to targets that do not exist: {unknown}. "
        "A wrong instruction costs more than a missing one."
    )

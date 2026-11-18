"""A prompt that sends an agent to a knowledge base's index names the file the runner writes.

ADR 0174 renamed a `files` base's `INDEX.md` to `index.md` (OKF reserves the lower-case name), and two
role prompts kept sending agents to `INDEX.md`, a file no run has any more: on a case-sensitive file
system the agent finds nothing. Every shipped prompt or template that mentions `.choregos/knowledge`
names the index exactly as `tranibor_runner.knowledge.INDEX` does.
"""

from __future__ import annotations

import pathlib
import re

from tranibor_runner.knowledge import INDEX, ROOT

REPO = pathlib.Path(__file__).resolve().parents[2]
#: Where the prompts and templates an agent reads are shipped from.
SHIPPED = [REPO / "packages", REPO / "apps" / "orchestrator" / "src", REPO / "templates"]
SUFFIXES = {".md", ".j2", ".jinja", ".txt", ".yaml", ".yml"}
NAMED = re.compile(r"\bindex\.md\b", re.IGNORECASE)


def _prompts() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for top in SHIPPED:
        for path in top.rglob("*"):
            shipped = path.suffix in SUFFIXES and path.is_file() and "tests" not in path.parts
            if shipped and ROOT in path.read_text("utf-8", errors="replace"):
                found.append(path)
    return found


def test_the_role_prompts_are_read() -> None:
    names = {p.relative_to(REPO).as_posix() for p in _prompts()}
    assert any(n.endswith("roles/brief/prompt.md") for n in names), "the test checks nothing"


def test_every_prompt_names_the_index_the_runner_writes() -> None:
    wrong = {
        f"{path.relative_to(REPO)}: `{name}`"
        for path in _prompts()
        for name in NAMED.findall(path.read_text("utf-8", errors="replace"))
        if name != INDEX
    }
    assert not wrong, f"prompts naming a knowledge index the runner does not write ({INDEX}): {sorted(wrong)}"

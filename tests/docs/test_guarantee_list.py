"""The concepts page names every guarantee of the core (S22-20).

The "Gates shipped today" list of `docs/concepts.md` had fallen behind: `outputs_in`,
`markdown_sections` and `action_succeeded` shipped, absent from the page. A guarantee that the
reference documentation does not name does not exist for whoever writes a workflow.
"""

from __future__ import annotations

import re
from pathlib import Path

from choregos_core.gates import _REGISTRY

CONCEPTS = Path(__file__).resolve().parents[2] / "docs" / "concepts.md"


def test_every_core_guarantee_is_named_in_the_concepts() -> None:
    # The core's guarantees only: an extension or a test can register others.
    core_names = {name for name, fn in _REGISTRY.items() if fn.__module__ == "choregos_core.gates"}
    assert "adr_number_free" in core_names
    block = re.search(r"Gates shipped today:\n\n```\n(.*?)```", CONCEPTS.read_text(encoding="utf-8"), re.S)
    assert block is not None, "the list of guarantees left docs/concepts.md"
    named = {name.strip() for name in re.split(r"[·\n]", block.group(1)) if name.strip()}
    assert sorted(core_names - named) == [], "guarantees missing from docs/concepts.md"
    assert sorted(named - core_names) == [], "named guarantees that do not exist"

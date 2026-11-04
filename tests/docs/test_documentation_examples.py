"""The examples of the documentation validate against the real models.

Wrong documentation costs more than no documentation: it sends someone into the wall with
confidence. The YAML blocks of the guides are therefore re-read by the DSL parser and by the
policy model, the same ones the platform uses.

This test has already paid off: the first draft of the English guide gave
`ticket_usd: { default: 8 }`, while that budget is indexed by ticket SIZE.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_contracts import Policy
from choregos_core import parse_workflow

DOCS = sorted((Path(__file__).resolve().parents[2] / "docs").glob("*.md"))
BLOCK = re.compile(r"```yaml\n(.*?)```", re.S)


def _blocks() -> list[tuple[str, str, dict[str, Any]]]:
    found: list[tuple[str, str, dict[str, Any]]] = []
    for page in DOCS:
        for index, block in enumerate(BLOCK.findall(page.read_text(encoding="utf-8"))):
            document = yaml.safe_load(block)
            if isinstance(document, dict) and document.get("kind") in {"Workflow", "Policy"}:
                found.append((f"{page.name}#{index}", block, document))
    return found


def test_there_are_examples_to_check() -> None:
    """Without this guard, moving the pages of `docs/` would make the suite green and empty."""
    assert _blocks(), "no example found in docs/: the test checks nothing any more"


@pytest.mark.parametrize(
    ("name", "block", "document"), _blocks(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_a_documentation_example_is_valid(name: str, block: str, document: dict[str, Any]) -> None:
    if document["kind"] == "Policy":
        Policy.model_validate(document)
        return
    _workflow, report = parse_workflow(block, strict=False)
    assert report.valid, f"{name}: " + " · ".join(i.format() for i in report.errors)
    assert not report.warnings, f"{name}: " + " · ".join(i.format() for i in report.warnings)


def test_the_cli_reference_is_up_to_date() -> None:
    """`docs/cli.md` is generated from the CLI: a command added without `make docs-cli` shows up here."""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "tools" / "gen_cli_reference.py"), "--check"],
        capture_output=True,
        text=True,
        env={**os.environ, "COLUMNS": "100"},
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout

"""An agent declares the connector kinds it works through (ADR 0181 C1): `needs: [scm]`.

What a project must connect was guessed from an agent's role name: `implement`, `review`, `fix_ci`…
required an `scm`, a guess of one trade in the core. A workflow now declares it; a workflow stored
before `needs:` existed declares none, and keeps the guess.
"""

from __future__ import annotations

import copy
import glob
from pathlib import Path
from typing import Any

import pytest
import yaml
from tranibor_contracts import Workflow
from tranibor_core.dsl.requirements import declares_needs, requirements

ROOT = Path(__file__).resolve().parents[3]
SHIPPED = sorted(
    glob.glob(str(ROOT / "packages/core/src/tranibor_core/dsl/templates/*.yaml"))
    + glob.glob(str(ROOT / "templates/*/workflows/*.yaml"))
)

DESK = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: desk, version: 1 }
actors:
  writer: { type: agent, role: implement, needs: [] }
  filer: { type: agent, role: file, needs: [itsm] }
states:
  open: { display: Open }
  done: { display: Done, terminal: true }
transitions:
  - { id: t-write, from: open, to: done, by: writer }
  - { id: t-file, from: open, to: done, by: filer }
"""


def _capabilities(document: dict[str, Any]) -> set[str]:
    return {r.capability for r in requirements([Workflow.model_validate(document)])}


def test_an_agent_requires_what_it_declares_and_its_role_guesses_nothing() -> None:
    found = {r.capability: r.reasons for r in requirements([Workflow.model_validate(yaml.safe_load(DESK))])}
    assert "scm" not in found, "`implement` alone no longer requires a repository"
    assert found["itsm"] == ("desk: the agent filer (file) works through a `itsm`",)


def test_a_workflow_that_declares_no_needs_keeps_the_guess() -> None:
    document = yaml.safe_load(DESK)
    for actor in document["actors"].values():
        actor.pop("needs")
    assert not declares_needs(Workflow.model_validate(document))
    assert "scm" in _capabilities(document)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: Path(p).name)
def test_a_shipped_workflow_requires_what_the_guess_required(path: str) -> None:
    """The templates declare what the role guess said: no project's requirements move."""
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    stored_before = copy.deepcopy(document)
    for actor in stored_before.get("actors", {}).values():
        actor.pop("needs", None)
    assert _capabilities(document) == _capabilities(stored_before)
    agents = [a for a in document.get("actors", {}).values() if a.get("type") == "agent"]
    if any(a.get("needs") for a in agents):
        assert declares_needs(Workflow.model_validate(document))


def test_needs_names_each_kind_once_and_as_a_connector_kind() -> None:
    document = yaml.safe_load(DESK)
    document["actors"]["filer"]["needs"] = ["itsm", "itsm"]
    with pytest.raises(ValueError, match="more than once"):
        Workflow.model_validate(document)
    document["actors"]["filer"]["needs"] = ["IT SM"]
    with pytest.raises(ValueError):
        Workflow.model_validate(document)

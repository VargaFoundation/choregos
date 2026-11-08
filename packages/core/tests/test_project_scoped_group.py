"""`{project}` in a human actor's group (S59-01): resolved per project, the only placeholder, and not in
a train's approval group."""

from __future__ import annotations

from choregos_core import parse_workflow
from choregos_core.dsl.groups import group_for, matches_some, placeholder_error

BASE = """
apiVersion: choregos/v1
kind: Workflow
metadata: {{name: release, version: 1}}
initial: waiting
actors:
  people: {{type: human, group: "{group}"}}
states:
  waiting: {{display: Waiting, kind: wait}}
  done: {{display: Done, terminal: true}}
transitions:
  - {{id: t-ship, from: waiting, to: done, by: people}}
"""


def _codes(group: str) -> list[str]:
    _, report = parse_workflow(BASE.format(group=group), strict=False)
    return [issue.code for issue in report.errors]


def test_the_project_placeholder_is_resolved_and_matched() -> None:
    assert group_for("{project}-maintainers", "iris") == "iris-maintainers"
    assert group_for(" {project}-maintainers ", None) == "{project}-maintainers"
    assert matches_some("{project}-maintainers", {"iris-maintainers", "fondation"})
    assert not matches_some("{project}-maintainers", {"iris-developers"})
    assert placeholder_error("{project}-leads") is None


def test_only_the_project_placeholder_is_accepted() -> None:
    assert "actor.group_placeholder" not in _codes("{project}-maintainers")
    assert "actor.group_placeholder" in _codes("{team}-maintainers")
    assert "actor.group_placeholder" in _codes("{project-maintainers")


def test_a_train_approval_group_does_not_name_the_project() -> None:
    source = """
apiVersion: choregos/v1
kind: Workflow
metadata: {name: release, version: 1}
initial: merged
actors:
  captain: {type: human, group: "{project}-captains"}
states:
  merged: {display: Merged}
  live: {display: Live, production: true, terminal: true}
transitions:
  - {id: t-release, from: merged, to: live, via: release_train, train: {env: prod, approval: captain}}
"""
    _, report = parse_workflow(source, strict=False)
    assert "actor.group_placeholder" in [issue.code for issue in report.errors]

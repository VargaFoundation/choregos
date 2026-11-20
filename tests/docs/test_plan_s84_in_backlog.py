# SPDX-License-Identifier: Apache-2.0
"""S84-00: the procurement plan and the backlog describe the same stories, in an order that can run.

`docs/plan/PLAN-2026-11-20-procurement.md` is handed to parallel workers one story block at a time,
and its waves are only safe if every story's dependencies are done before it starts. Three properties:
every `S84` story heading of the plan is a backlog story, the backlog holds no `S84` story the plan
does not describe, and in the backlog every dependency of a story comes before it. "Before" is the
backlog's own order: a lower `sprint` (the plan's wave), or the same wave and a lower `priority`,
since stories of one wave and one stream are sequential.

Only the core's stories are read. The plan's `PA-NN` headings are stories of the agents repository:
they have no backlog entry, and their state is carried by the core story that ships their archive.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "plan" / "PLAN-2026-11-20-procurement.md"
BACKLOG = ROOT / "docs" / "plan" / "08-backlog.yaml"
STREAM = "S84"
#: `### S84-04 · feat(api): …`
HEADING = re.compile(rf"^### ({STREAM}-\d+[a-z]?) ·", re.M)
#: `### PA-02 · feat(procurement): …`
OTHER_REPOSITORY = re.compile(r"^### (PA-\d+) ·", re.M)


def _plan_stories() -> list[str]:
    return HEADING.findall(PLAN.read_text("utf-8"))


def _backlog_stories() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(BACKLOG.read_text("utf-8"))
    return {story["id"]: story for story in data["stories"] if story["stream"] == STREAM}


def _order(story: dict[str, Any]) -> tuple[int, int]:
    return int(story["sprint"]), int(story["priority"])


def test_every_plan_story_is_in_the_backlog() -> None:
    plan, backlog = _plan_stories(), _backlog_stories()
    assert plan, "no story heading found in the plan: the test checks nothing"
    assert len(plan) == len(set(plan)), "a story heading appears twice in the plan"
    missing = sorted(set(plan) - set(backlog))
    assert not missing, f"plan stories missing from 08-backlog.yaml: {missing}"


def test_the_backlog_has_no_story_the_plan_lacks() -> None:
    extra = sorted(set(_backlog_stories()) - set(_plan_stories()))
    assert not extra, f"backlog stories of stream {STREAM} the plan does not describe: {extra}"


def test_every_dependency_comes_before_its_story() -> None:
    backlog = _backlog_stories()
    late = []
    for sid, story in backlog.items():
        for dep in story["depends_on"]:
            assert dep in backlog, f"{sid} depends on {dep}, which is not a backlog story of {STREAM}"
            if _order(backlog[dep]) >= _order(story):
                late.append(f"{sid} {_order(story)} is not after {dep} {_order(backlog[dep])}")
    assert not late, f"dependencies that do not come before their story (wave, priority): {late}"


def test_the_stories_of_the_other_repository_stay_out_of_the_backlog() -> None:
    elsewhere = OTHER_REPOSITORY.findall(PLAN.read_text("utf-8"))
    assert elsewhere, "the plan names no story of the agents repository: the pattern no longer matches"
    data = yaml.safe_load(BACKLOG.read_text("utf-8"))
    listed = sorted(story["id"] for story in data["stories"] if story["id"] in set(elsewhere))
    assert not listed, f"stories of the agents repository listed in this repository's backlog: {listed}"

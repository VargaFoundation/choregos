"""The plan of 2026-10-27 and the backlog describe the same stories, in an order that can be executed.

`docs/plan/PLAN-2026-10-27.md` is handed to parallel agents one story block at a time, and its
waves are only safe if every story's dependencies sit in an earlier wave. Two properties:
every story heading of the plan is a backlog story, and in the backlog every dependency of a
stream S45-S47 story has a lower sprint (the plan's wave) than the story itself. Only this plan's
streams are read: its prose also cites main's own S43 and S44 stories, which have no backlog entry.
"""

from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "plan" / "PLAN-2026-10-27.md"
BACKLOG = ROOT / "docs" / "plan" / "08-backlog.yaml"
#: `### S46-08 · feat(api): …`
HEADING = re.compile(r"^### (S4[5-7]-\d+[a-z]?) ·", re.M)
STREAMS = ("S45", "S46", "S47")


def _plan_stories() -> list[str]:
    return HEADING.findall(PLAN.read_text("utf-8"))


def _backlog_stories() -> dict[str, dict[str, object]]:
    data = yaml.safe_load(BACKLOG.read_text("utf-8"))
    return {story["id"]: story for story in data["stories"]}


def test_every_plan_story_is_in_the_backlog() -> None:
    plan, backlog = _plan_stories(), _backlog_stories()
    assert plan, "no story heading found in the plan: the test checks nothing"
    assert len(plan) == len(set(plan)), "a story heading appears twice in the plan"
    missing = sorted(set(plan) - set(backlog))
    assert not missing, f"plan stories missing from 08-backlog.yaml: {missing}"


def test_the_backlog_has_no_story_the_plan_lacks() -> None:
    plan = set(_plan_stories())
    extra = sorted(sid for sid in _backlog_stories() if sid[:3] in STREAMS and sid not in plan)
    assert not extra, f"backlog stories of streams S45-S47 the plan does not describe: {extra}"


def test_every_dependency_is_in_an_earlier_wave() -> None:
    backlog = _backlog_stories()
    late = []
    for sid, story in backlog.items():
        if sid[:3] not in STREAMS:
            continue
        for dep in story["depends_on"]:
            assert dep in backlog, f"{sid} depends on {dep}, which is not a backlog story"
            if backlog[dep]["sprint"] >= story["sprint"]:
                late.append(f"{sid} (wave {story['sprint']}) after {dep} (wave {backlog[dep]['sprint']})")
    assert not late, f"dependencies that are not in an earlier wave: {late}"

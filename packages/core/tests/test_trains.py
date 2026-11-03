"""What a workflow asks of the train it takes (ADR 0041, S21-21).

`train: {env: prod, approval: captain}` was written and read by nobody; the GitHub webhook
shipped every merged work item to prod, a study included.
"""

from __future__ import annotations

from choregos_core import parse_workflow
from choregos_core.dsl import load_template
from choregos_core.dsl.trains import train_after_merge, train_approval, train_env

STUDY = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: study, version: 1 }
actors:
  architect: { type: agent, role: architect, model: "profile:standard" }
  platform: { type: system }
states:
  inbox: { display: Inbox, kind: wait }
  written: { display: ADR written }
  pr_open: { display: PR open }
  merged: { display: Merged, terminal: true }
transitions:
  - { id: t-write, from: inbox, to: written, by: architect, outputs: [adr_markdown] }
  - { id: t-pr, from: written, to: pr_open, by: platform, does: open_pr }
  - { id: t-merge, from: pr_open, to: merged, by: platform, does: merge_pr, gates: [ci_green] }
"""

DELIVERY = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: delivery, version: 1 }
actors:
  developer: { type: agent, role: implement, model: "profile:standard" }
  platform: { type: system }
  owner: { type: human, group: maintainers, sla_hours: 8 }
  robot: { type: agent, role: verify_prod, model: "profile:standard" }
states:
  inbox: { display: Inbox, kind: wait }
  done: { display: Done }
  shipped: { display: Shipped }
  live: { display: Live, production: true }
  checked: { display: Checked, terminal: true }
  merged_by_hand: { display: Merged by hand }
transitions:
  - { id: t-human-merge, from: inbox, to: merged_by_hand, by: owner }
  - { id: t-hand-train, from: merged_by_hand, to: live, via: release_train, train: { env: staging } }
  - { id: t-dev, from: inbox, to: done, by: developer, outputs: [summary] }
  - { id: t-ship, from: done, to: shipped, by: platform, does: merge_pr }
  - { id: t-live, from: shipped, to: live, via: release_train, train: { env: prod, approval: robot } }
  - { id: t-check, from: live, to: checked, by: robot, outputs: [verdict] }
"""


def test_the_default_template_ships_to_prod_under_the_captains_approval() -> None:
    workflow = load_template("default-simple")
    train = train_after_merge(workflow)
    assert train is not None and train.id == "t-deploy"
    assert train_env(train) == "prod"
    assert train_approval(workflow, train) == {"required": True, "group": "release-captains"}


def test_a_prudent_delivery_goes_through_staging_first_without_approval() -> None:
    workflow = load_template("advanced")
    train = train_after_merge(workflow)
    assert train is not None and train_env(train) == "staging"
    assert train_approval(workflow, train) == {"required": False, "group": None}


def test_a_study_that_merges_an_adr_takes_no_train() -> None:
    assert train_after_merge(parse_workflow(STUDY, strict=False)[0]) is None


def test_only_a_platform_merge_counts_and_a_non_human_approval_is_still_required() -> None:
    """`t-ship` merges through `does`, without a `merged*` state; `merged_by_hand`, declared BEFORE, is
    reached by a human: it is not a platform merge, its train is not the webhook's.
    The approval named by an agent is required, without a group: the policy's will decide."""
    workflow = parse_workflow(DELIVERY, strict=False)[0]
    train = train_after_merge(workflow)
    assert train is not None and train.id == "t-live"
    assert train_approval(workflow, train) == {"required": True, "group": None}

    without_does = parse_workflow(DELIVERY.replace(", does: merge_pr", ""), strict=False)[0]
    assert train_after_merge(without_does) is None, "neither `does` nor a platform merge: no train"


def test_a_train_without_approval_requires_nothing_of_the_departure() -> None:
    workflow = parse_workflow(DELIVERY.replace(", approval: robot", ""), strict=False)[0]
    train = train_after_merge(workflow)
    assert train is not None and train_env(train) == "prod"
    assert train_approval(workflow, train) == {"required": False, "group": None}

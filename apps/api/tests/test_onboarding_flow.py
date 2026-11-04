"""The GitHub webhook boards a merged work item into the train of ITS workflow (ADR 0041, S21-21).

It used to board every merged work item into the prod train: a study that merges an ADR went to
production, a careful delivery skipped its staging, and the approval the workflow requires never
reached the train.
"""

from __future__ import annotations

import json
from typing import Any

from choregos_core.dsl import template_yaml
from httpx import AsyncClient

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


async def _post(client: AsyncClient, event: str, delivery: str, body: dict[str, Any]) -> None:
    response = await client.post(
        "/api/v1/webhooks/github",
        content=json.dumps(body).encode(),
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "content-type": "application/json",
        },
    )
    assert response.status_code == 202, response.text


async def _merged_work_item(client: AsyncClient, number: int, labels: list[str]) -> None:
    repo = {"full_name": "varga/billing-api"}
    issue = {"number": number, "title": f"work item {number}", "labels": [{"name": n} for n in labels]}
    who = {"login": "a"}
    labeling = {"action": "labeled", "repository": repo, "issue": issue, "label": {"name": "agent-ready"}}
    await _post(client, "issues", f"labeled-{number}", {**labeling, "sender": who})
    pr = {
        "number": 100 + number,
        "merged": True,
        "html_url": f"https://github.com/varga/billing-api/pull/{number}",
        "head": {"ref": f"choregos/{number}-x", "sha": f"sha{number}"},
        "base": {"ref": "main"},
        "labels": [],
    }
    merge = {"action": "closed", "repository": repo, "pull_request": pr, "sender": who}
    await _post(client, "pull_request", f"merge-{number}", merge)


def _boardings() -> dict[str, list[dict[str, Any]]]:
    from choregos_api.temporal import get_temporal

    trains: dict[str, list[dict[str, Any]]] = {}
    for workflow_id, name, payload in get_temporal().signals:  # type: ignore[attr-defined]
        if name == "merged":
            trains.setdefault(workflow_id, []).append(payload)
    return trains


async def test_each_merged_work_item_boards_the_train_of_its_own_workflow(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    careful_yaml = template_yaml("advanced").replace("name: advanced", "name: careful", 1)
    for name, text in (("study", STUDY), ("careful", careful_yaml)):
        published = await client.put(f"/api/v1/projects/{pid}/workflows/{name}", json={"yaml": text})
        assert published.status_code in {200, 201}, published.text
    routing = {
        "default": "default-simple",
        "rules": [
            {"when": {"labels_any": ["adr"]}, "workflow": "study"},
            {"when": {"labels_any": ["careful"]}, "workflow": "careful"},
        ],
    }
    assert (await client.put(f"/api/v1/projects/{pid}/workflow-routing", json=routing)).status_code == 200

    await _merged_work_item(client, 1, ["agent-ready"])
    await _merged_work_item(client, 2, ["agent-ready", "adr"])
    await _merged_work_item(client, 3, ["agent-ready", "careful"])

    trains = _boardings()
    assert set(trains) == {"train-billing-api-prod", "train-billing-api-staging"}, trains
    (prod,) = trains["train-billing-api-prod"]
    assert prod["work_item_key"] == "varga/billing-api#1"
    assert prod["approval"] == {"required": True, "group": "release-captains"}, (
        "the captain of default-simple"
    )
    (staging,) = trains["train-billing-api-staging"]
    assert staging["work_item_key"] == "varga/billing-api#3", (
        "the careful delivery goes through staging first"
    )
    assert staging["approval"] == {"required": False, "group": None}
    assert all(c["work_item_key"] != "varga/billing-api#2" for cs in trains.values() for c in cs), (
        "a study that merges an ADR goes nowhere"
    )

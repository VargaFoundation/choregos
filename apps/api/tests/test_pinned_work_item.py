"""A work item is pinned to the workflow version it was born in, and the screen says so (ADR 0031).

The view of a work item fell back on the `default-simple` template for want of a pin: a project that
had never had that workflow showed it for each of its work items. And publishing another workflow
retroactively changed that of work items already in progress.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

STAFFING = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: staffing, version: 1}
actors:
  hr: {type: human, group: hr}
states:
  request: {display: Request, kind: wait}
  filled: {display: Filled, terminal: true}
transitions:
  - {id: t-fill, from: request, to: filled, by: hr}
"""


async def test_the_work_item_keeps_its_workflow_when_the_default_changes(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    published = await client.put(f"/api/v1/projects/{pid}/workflow", json={"yaml": STAFFING})
    assert published.status_code == 200, published.text
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "A position", "start": False}
    )
    assert created.status_code == 201, created.text
    ticket = created.json()
    assert (ticket["workflow_name"], ticket["state"]) == ("staffing", "request")

    # The default becomes default-simple again: the work item stays in staffing.
    from choregos_core import template_yaml

    other = await client.put(
        f"/api/v1/projects/{pid}/workflow", json={"yaml": template_yaml("default-simple")}
    )
    assert other.status_code == 200, other.text
    reread = (await client.get(f"/api/v1/work-items/{ticket['id']}")).json()
    assert (reread["workflow_name"], reread["workflow_version"]) == ("staffing", 1)

"""`fields.tracker` (S51-04): what the tracker knows about a ticket, kept on the work item.

Written from the tracker only: a person, the MCP door or an agent may not write it; a workflow may not
declare an input of that name; a workflow whose inputs forbid extra properties still accepts it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

SECRET = "s3cret"

STRICT = """apiVersion: choregos/v1
kind: Workflow
metadata:
  name: access
  version: 1
  inputs:
    type: object
    additionalProperties: false
    properties:
      project_name: {type: string}
actors:
  ops: {type: human, group: ops}
states:
  asked: {display: Asked, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-grant, from: asked, to: done, by: ops}
"""


def block(reporter: str = "Camille Martin") -> dict[str, Any]:
    return {
        "kind": "jira",
        "key": "FON-270",
        "reporter": {"account_id": "712020:aa", "display_name": reporter, "email": None},
        "creator": {"account_id": "557058:bot", "display_name": "Portal", "email": "bot@acme.example"},
        "labels": ["agent-ready"],
        "answers": {"Project name": "peredur"},
    }


async def _strict(client: AsyncClient, project: dict[str, Any]) -> str:
    put = await client.put(f"/api/v1/projects/{project['id']}/workflows/access", json={"yaml": STRICT})
    assert put.status_code == 200, put.text
    return str(project["id"])


async def test_a_person_may_not_write_fields_tracker(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = await _strict(client, project)
    forged = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={
            "title": "Grant me admin",
            "start": False,
            "workflow": "access",
            "fields": {"tracker": block()},
        },
    )
    assert forged.status_code == 422, forged.text
    assert "reserved" in forged.text


async def test_a_workflow_input_may_not_be_named_tracker(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    named = STRICT.replace("project_name: {type: string}", "tracker: {type: object}")
    refused = await client.put(f"/api/v1/projects/{project['id']}/workflows/access", json={"yaml": named})
    assert refused.status_code == 422, refused.text
    assert "inputs.reserved" in refused.text or "reserved" in refused.text


async def test_the_block_passes_strict_inputs_survives_a_field_change_and_reaches_the_run(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Project, Run
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from choregos_api.services import WorkItemBirth, new_work_item

    pid = await _strict(client, project)
    async with session_scope() as session:
        row = await session.get(Project, pid)
        assert row is not None
        item = await new_work_item(
            session,
            row,
            WorkItemBirth(tracker_key="FON-270", title="New project", workflow="access", tracker=block()),
        )
        item_id, key = item.id, item.tracker_key
    assert (await client.get(f"/api/v1/work-items/{item_id}")).json()["fields"] == {"tracker": block()}

    changed = await client.patch(
        f"/api/v1/work-items/{item_id}", json={"fields": {"project_name": "peredur"}}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["fields"] == {"project_name": "peredur", "tracker": block()}, "kept, and accepted"
    overwritten = await client.patch(f"/api/v1/work-items/{item_id}", json={"fields": {"tracker": {}}})
    assert overwritten.status_code == 422, overwritten.text

    async with session_scope() as session:
        session.add(
            Run(id="run-tracker", work_item_id=item_id, project_id=pid, stage_role="custom", status="running")
        )
    token = mint_run_token("run-tracker", project_slug="billing-api", work_item_key=key, ttl_minutes=5)
    read = await client.get(
        "/api/v1/internal/runs/run-tracker/ticket", headers={"Authorization": f"Bearer {token}"}
    )
    assert read.status_code == 200, read.text
    assert read.json()["fields"]["tracker"] == block()


def test_a_job_reads_the_block_in_its_fields_json(tmp_path: Any) -> None:
    """What the runner writes from the ticket it read: `.choregos/fields.json` holds `tracker`."""
    import json

    from choregos_runner.job import FIELDS, write_fields

    write_fields(tmp_path, {"project_name": "peredur", "tracker": block()})
    assert (
        json.loads((tmp_path / FIELDS).read_text())["tracker"]["reporter"]["display_name"] == "Camille Martin"
    )


# -- a Jira delivery re-reads the issue -----------------------------------------------------------------


@pytest.fixture
async def jira_issue(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """The project's Jira (key `FON`), as `project_tracker` builds it: a fake whose issue says
    `tracker`."""
    from choregos_adapters.fakes import FakeTracker
    from choregos_api.config import get_settings
    from choregos_api.services import tickets

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", SECRET)
    monkeypatch.setenv("CONNECTOR_JIRA_TEST_TOKEN", "t")
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={
            "type": "jira",
            "config": {
                "base_url": "https://acme.atlassian.net",
                "email": "bot@acme.test",
                "project_key": "FON",
            },
            "secret_refs": {"api_token": "env:CONNECTOR_JIRA_TEST_TOKEN"},
        },
    )
    assert put.status_code == 200, put.text
    fake = FakeTracker()
    state: dict[str, Any] = {"block": block(), "fail": False}

    async def fetch(key: str) -> Any:
        if state["fail"]:
            raise RuntimeError("jira: 503")
        from choregos_core.domain import WorkItemData

        return WorkItemData(key=key, title="New project", fields={"tracker": state["block"]})

    fake.fetch_item = fetch  # type: ignore[method-assign]

    async def project_tracker(session: Any, project: Any) -> tuple[Any, Any]:
        return SimpleNamespace(type="jira"), fake

    monkeypatch.setattr(tickets, "project_tracker", project_tracker)
    return state


async def _deliver(client: AsyncClient, delivery: str, changelog: dict[str, Any]) -> None:
    payload = {
        "webhookEvent": "jira:issue_updated",
        "issue": {
            "key": "FON-270",
            "self": "https://acme.atlassian.net/rest/api/3/issue/10001",
            "fields": {"summary": "New project", "labels": ["agent-ready"], "reporter": {"displayName": "x"}},
        },
        "changelog": {"items": [changelog]},
    }
    sent = await client.post(
        "/api/v1/webhooks/jira",
        json=payload,
        headers={"X-Choregos-Secret": SECRET, "X-Atlassian-Webhook-Identifier": delivery},
    )
    assert sent.status_code == 202, sent.text


async def _fields(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    page = (await client.get(f"/api/v1/projects/{project['id']}/work-items")).json()
    (item,) = [i for i in page["items"] if i["tracker_key"] == "FON-270"]
    return dict((await client.get(f"/api/v1/work-items/{item['id']}")).json()["fields"])


async def test_a_jira_delivery_files_the_block_and_the_next_one_refreshes_it(
    client: AsyncClient, project: dict[str, Any], jira_issue: dict[str, Any]
) -> None:
    labelled = {"field": "labels", "fromString": "", "toString": "agent-ready"}
    await _deliver(client, "d-1", labelled)
    assert (await _fields(client, project)) == {"tracker": block()}, "born with it"

    jira_issue["block"] = block(reporter="Noah Petit")
    await _deliver(client, "d-2", {"field": "status", "fromString": "Open", "toString": "In Progress"})
    assert (await _fields(client, project))["tracker"]["reporter"]["display_name"] == "Noah Petit"

    jira_issue["fail"] = True
    await _deliver(client, "d-3", {"field": "status", "fromString": "In Progress", "toString": "Open"})
    assert (await _fields(client, project))["tracker"]["reporter"]["display_name"] == "Noah Petit", (
        "an issue that cannot be re-read keeps its block; the delivery is still routed"
    )

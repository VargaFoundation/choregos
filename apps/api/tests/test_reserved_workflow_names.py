"""A new workflow may not be named after a console page (S55-15).

A workflow's page is `/p/<slug>/workflows/<name>`, and the console's own pages there win over it:
`library` and `new` are pages, `catalogue` redirects to the library (S55-04). A workflow of that name
was published, listed, and could never be opened.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


def _workflow(name: str) -> str:
    return f"""apiVersion: choregos/v1
kind: Workflow
metadata: {{name: {name}, version: 1}}
actors:
  owner: {{type: human, group: owners}}
states:
  asked: {{display: Asked, kind: wait}}
  done: {{display: Done, terminal: true}}
transitions:
  - {{id: t-do, from: asked, to: done, by: owner}}
"""


async def _put(client: AsyncClient, pid: str, name: str, **extra: Any) -> Any:
    return await client.put(
        f"/api/v1/projects/{pid}/workflows/{name}", json={"yaml": _workflow(name), **extra}
    )


def _reserved(response: Any) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert "is reserved" in body["detail"]
    assert body["errors"] == [
        {"loc": ["metadata", "name"], "msg": body["errors"][0]["msg"], "code": "workflow.name_reserved"}
    ]


@pytest.mark.parametrize("name", ["library", "new", "catalogue"])
async def test_creating_a_workflow_named_after_a_console_page_is_refused(
    client: AsyncClient, project: dict[str, Any], name: str
) -> None:
    pid = project["id"]
    _reserved(await _put(client, pid, name, create_only=True))
    _reserved(await _put(client, pid, name))
    listed = {w["name"] for w in (await client.get(f"/api/v1/projects/{pid}/workflows")).json()}
    assert name not in listed, "nothing was published"
    # A name that only starts like one is free.
    assert (await _put(client, pid, f"{name}-intake", create_only=True)).status_code == 200


async def test_adopting_a_library_entry_under_a_reserved_name_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    refused = await client.post(
        f"/api/v1/projects/{project['id']}/workflow-adoptions",
        json={"source": "core", "name": "default-request", "as": "library"},
    )
    _reserved(refused)


async def test_a_workflow_published_under_that_name_before_keeps_its_next_versions(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project that already has one is not locked out of it: its next version still publishes."""
    from choregos_api.services import definitions

    pid = project["id"]
    with monkeypatch.context() as before:
        before.setattr(definitions, "RESERVED_WORKFLOW_NAMES", frozenset())
        assert (await _put(client, pid, "library")).status_code == 200
    again = await _put(client, pid, "library")
    assert again.status_code == 200, again.text
    assert again.json()["version"] == 2

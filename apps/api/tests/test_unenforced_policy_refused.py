"""Governance the platform does not enforce is refused when written, never when read (ADR 0101)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from httpx import AsyncClient

PROMISING_POLICY = """
apiVersion: choregos/v1
kind: Policy
metadata: { name: promising, version: 1 }
approvals:
  merge: { required: always, group: maintainers }
"""

WORKFLOW_WITH_REVIEW_HUMANS = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: stored, version: 1 }
actors:
  worker: { type: agent, role: implement }
states:
  todo: { display: To do }
  done: { display: Done, terminal: true }
transitions:
  - id: t-work
    from: todo
    to: done
    by: worker
    review: { agents: [worker], humans: { group: maintainers, required: always } }
"""


async def test_a_policy_promising_an_approval_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    response = await client.put(f"/api/v1/projects/{project['id']}/policy", json={"yaml": PROMISING_POLICY})
    assert response.status_code == 422, response.text
    body = response.json()
    assert [e["code"] for e in body["errors"]] == ["policy.approval_not_enforced"]
    assert "approvals.merge" in body["detail"]
    assert "ADR 0101" in body["detail"]


async def _new_project(session: Any, slug: str) -> Any:
    from choregos_api.db.models import Organization, Project

    org = Organization(slug=slug, name=slug)
    session.add(org)
    await session.flush()
    project = Project(
        org_id=org.id, slug=slug, name=slug, status="active", config={"slug": slug, "org": slug}
    )
    session.add(project)
    await session.flush()
    return project


async def test_a_lazy_provisioning_does_not_refuse(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.errors import ApiError
    from choregos_api.services import definitions, gabarits

    real = gabarits.livraison

    def promising(*args: Any, **kwargs: Any) -> Any:
        return dataclasses.replace(real(*args, **kwargs), policy=PROMISING_POLICY)

    monkeypatch.setattr(gabarits, "livraison", promising)

    async with session_scope(orgs="*") as session:
        project = await _new_project(session, "lazy")
        _, policy = await definitions.ensure_defaults(session, project)
        assert policy.name == "promising"  # stored, and no 422 on a read path

    async with session_scope(orgs="*") as session:
        project = await _new_project(session, "strict")
        with pytest.raises(ApiError) as refused:
            await definitions.ensure_defaults(session, project, refuse_unenforced=True)
    assert refused.value.status_code == 422


async def test_a_stored_workflow_with_review_humans_still_loads(app: Any) -> None:
    from choregos_api.db.models import WorkflowDef
    from choregos_api.services.definitions import workflow_model

    row = WorkflowDef(project_id="p", name="stored", version=1, yaml=WORKFLOW_WITH_REVIEW_HUMANS, json_doc={})
    assert workflow_model(row).transitions[0].review is not None

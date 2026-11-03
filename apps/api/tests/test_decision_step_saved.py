"""A decision step its project cannot run is refused when the workflow is saved (ADR 0121 §4, S52-04).

`decision.gateway_unsupported`: the project's gateway serves no `/v1/systemone` (an
`openai-compatible` one, say). `decision.model_usage`: the organisation's catalogue does not enable
the step's model for `decide`. The error is the author's to read when they save, not a work item's
to discover at its first step.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: routed, version: 1 }
initial: inbox
actors:
  router:
    type: decision
    role: route_ticket
    state: [title, description]
    questions:
      route: { type: choice, instructions: "Which team handles it?", options: [billing, tech] }
  platform: { type: system }
states:
  inbox: { display: To do }
  routed: { display: Routed }
  billing: { display: Billing, terminal: true }
  other: { display: Other, terminal: true }
transitions:
  - { id: t-route, from: inbox, to: routed, by: router }
  - id: t-billing
    from: routed
    to: billing
    by: platform
    when: { route: billing, route_confidence: { gte: 0.8 } }
  - { id: t-other, from: routed, to: other, by: platform }
"""


async def _catalogue(usages: list[str]) -> None:
    from choregos_api.db.models import ModelCatalogueRow, Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        session.add(ModelCatalogueRow(org_id=org.id, model_name="platform/decide", usages=usages))


async def _save(client: AsyncClient, project: dict[str, Any]) -> Any:
    return await client.put(f"/api/v1/projects/{project['id']}/workflows/routed", json={"yaml": WORKFLOW})


async def test_a_decision_step_its_project_can_run_is_saved(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _catalogue(["decide"])
    saved = await _save(client, project)
    assert saved.status_code == 200, saved.text


@pytest.mark.parametrize("usages", [None, ["agent"]], ids=["no entry", "a chat model"])
async def test_a_model_not_enabled_for_decide_is_refused_at_save(
    client: AsyncClient, project: dict[str, Any], usages: list[str] | None
) -> None:
    if usages is not None:
        await _catalogue(usages)
    refused = await _save(client, project)
    assert refused.status_code == 422, refused.text
    (error,) = refused.json()["errors"]
    assert error["code"] == "decision.model_usage"
    assert error["loc"] == ["/actors/router/model"]
    assert "decide" in error["msg"]


async def test_a_gateway_without_the_decision_route_is_refused_at_save(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The project's own `openai-compatible` gateway, built for real (the fakes declare every route)."""
    from choregos_adapters.registry import _REGISTRY
    from choregos_api.db.models import Connector
    from choregos_api.db.session import session_scope
    from choregos_api.services import gateway as gateway_service

    monkeypatch.setattr(
        gateway_service, "build", lambda kind, type_name, config: _REGISTRY[(kind, type_name)](config)
    )
    await _catalogue(["decide"])
    async with session_scope() as session:
        session.add(
            Connector(
                project_id=project["id"],
                kind="gateway",
                type="openai-compatible",
                config={"base_url": "https://llm.example/v1"},
                secret_refs={},
            )
        )
    refused = await _save(client, project)
    assert refused.status_code == 422, refused.text
    (error,) = refused.json()["errors"]
    assert error["code"] == "decision.gateway_unsupported"
    assert error["loc"] == ["/actors/router"]
    assert "systemone" in error["msg"] and "OpenAiCompatibleGateway" in error["msg"]


async def test_a_workflow_without_a_decision_step_is_not_checked(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No catalogue entry, a gateway that cannot be built: neither matters to a workflow that asks
    no decision."""
    from choregos_api.services import gateway as gateway_service

    async def unbuildable(*_: Any) -> Any:
        raise AssertionError("the gateway is not built for a workflow without a decision step")

    monkeypatch.setattr(gateway_service, "project_gateway", unbuildable)
    without = WORKFLOW.replace("by: router", "by: platform").replace(
        "    when: { route: billing, route_confidence: { gte: 0.8 } }\n", ""
    )
    saved = await client.put(f"/api/v1/projects/{project['id']}/workflows/routed", json={"yaml": without})
    assert saved.status_code == 200, saved.text

# SPDX-License-Identifier: Apache-2.0
"""The `decision-triage` core template (S52-07, ADR 0121): classify, route, escalate below a threshold.

Played on the in-memory engine with the scripted decision model: a sure `deliver` goes to work, a sure
`clarify` waits for the requester, and anything unsure — or a `review` — goes to a person.
"""

from __future__ import annotations

import pytest
from choregos_core.dsl import template_yaml

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


async def _publish(setup: Fixture) -> None:
    from choregos_api.db.models import ModelCatalogueRow, Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import SYSTEM
    from choregos_api.services.definitions import publier_workflow

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        session.add(ModelCatalogueRow(org_id=project.org_id, model_name="platform/decide", usages=["decide"]))
        await session.flush()
        definition = await publier_workflow(session, SYSTEM, project, template_yaml("decision-triage"))
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id


@pytest.mark.parametrize(
    ("answer", "probability", "route"),
    [
        pytest.param("deliver", 0.92, ["t-classify", "t-route-deliver"], id="sure deliver goes to work"),
        pytest.param(
            "clarify", 0.9, ["t-classify", "t-route-clarify"], id="sure clarify waits for the requester"
        ),
        pytest.param("deliver", 0.6, ["t-classify", "t-route-person"], id="unsure goes to a person"),
        pytest.param("review", 0.99, ["t-classify", "t-route-person"], id="review always goes to a person"),
    ],
)
async def test_the_template_routes_what_it_is_sure_of_and_escalates_the_rest(
    setup: Fixture, answer: str, probability: float, route: list[str]
) -> None:
    from choregos_orchestrator.local import run_local

    await _publish(setup)
    setup.adapters.decisions.answer(route=(answer, probability))
    trace = await run_local(setup.project_id, setup.work_item_id, max_steps=2)
    assert [s.transition for s in trace.steps] == route
    assert trace.steps[0].kind == "decision"

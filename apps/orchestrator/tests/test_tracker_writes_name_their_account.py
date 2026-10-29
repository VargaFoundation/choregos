"""Each tracker write of the bookkeeping names its connector and account (S42-06g, ADR 0107).

The owner's question of 2026-10-28: a workflow moves a Jira ticket, "API or MCP? With my account or a
service account?". The platform writes the bookkeeping through the project's tracker connector; the
events it records now say which one, and under which account, so that the console can show it.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import EventType

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

JIRA = {"base_url": "https://acme.atlassian.net", "email": "bot@acme.example", "project_key": "XXX"}


async def _tracker(setup: Fixture, type_name: str, config: dict[str, Any]) -> None:
    from choregos_api.db.models import Connector
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(Connector(project_id=setup.project_id, kind="tracker", type=type_name, config=config))


async def _payloads(setup: Fixture, type_: EventType) -> list[dict[str, Any]]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Event).where(Event.work_item_id == setup.work_item_id, Event.type == str(type_))
            )
        ).scalars()
        return [dict(row.payload) for row in rows]


async def _mirror(setup: Fixture) -> dict[str, Any]:
    from choregos_orchestrator.activities.tracker import mirror_state

    await mirror_state(
        {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "state": "triage", "reason": "t"}
    )
    (payload,) = await _payloads(setup, EventType.WORKITEM_STATE_CHANGED)
    return payload


async def test_a_state_mirrored_to_jira_names_the_connectors_account(setup: Fixture) -> None:
    await _tracker(setup, "jira", JIRA)
    payload = await _mirror(setup)
    assert payload["via"] == {"type": "jira", "account": "bot@acme.example"}


async def test_a_question_posted_on_the_ticket_names_the_connectors_account(setup: Fixture) -> None:
    from choregos_orchestrator.activities.tracker import create_human_request

    await _tracker(setup, "jira", JIRA)
    await create_human_request(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-1",
            "kind": "question",
            "payload": {"question": "Which currency?"},
        }
    )
    (payload,) = await _payloads(setup, EventType.WORKITEM_HUMAN_REQUESTED)
    assert payload["via"] == {"type": "jira", "account": "bot@acme.example"}


async def test_a_tracker_whose_configuration_names_no_account_says_its_type(setup: Fixture) -> None:
    """A GitLab token names no account in its configuration: the type is still recorded."""
    await _tracker(setup, "gitlab-issues", {"base_url": "https://gitlab.example", "project": "acme/app"})
    payload = await _mirror(setup)
    assert payload["via"] == {"type": "gitlab-issues", "account": None}


async def test_a_project_without_a_tracker_connector_records_no_tracker_write(setup: Fixture) -> None:
    payload = await _mirror(setup)
    assert "via" not in payload

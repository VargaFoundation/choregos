"""Live pages (S59-08): every event persisted for a work item reaches the topics its page and its
project's pages listen to, as a signal (type, subject, run), and only those who may read the work item
or the project open the stream."""

from __future__ import annotations

import json
from typing import Any

from choregos_api.events import EventBus, sse_signal
from choregos_contracts import ChoregosEvent, EventType
from httpx import AsyncClient


def test_an_event_with_ids_is_filed_under_its_work_item_and_its_project() -> None:
    bus = EventBus()
    event = ChoregosEvent.emit(
        EventType.WORKITEM_STATE_CHANGED, source="/t", subject="FON-276",
        project_id="p-1", work_item_id="w-1", run_id="r-1", to="planned",
    )  # fmt: skip
    with bus.listen("work-item:w-1") as item, bus.listen("project-id:p-1") as project:
        bus.publish(event)
        assert item.get_nowait().id == event.id
        assert project.get_nowait().id == event.id


def test_the_signal_is_an_unnamed_message_without_the_payload() -> None:
    event = ChoregosEvent.emit(
        EventType.WORKITEM_STATE_CHANGED, source="/t", subject="FON-276", run_id="r-1", secret="x"
    )
    signal = sse_signal(event)
    assert "event" not in signal, "unnamed: a browser's onmessage receives it"
    assert json.loads(signal["data"]) == {"type": str(event.type), "subject": "FON-276", "run_id": "r-1"}


async def test_persist_event_carries_the_ids_to_the_bus(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.events import get_bus
    from choregos_api.services.events import persist_event

    created = await client.post(f"/api/v1/projects/{project['id']}/work-items", json={"title": "Live"})
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    with get_bus().listen(f"work-item:{item_id}") as queue:
        async with session_scope() as session:
            await persist_event(
                session, EventType.WORKITEM_STATE_CHANGED, project_id=project["id"], work_item_id=item_id,
                subject="X-1", to="planned",
            )  # fmt: skip
        event = queue.get_nowait()
    assert event.data["project_id"] == project["id"]


async def test_an_unknown_work_item_has_no_stream(client: AsyncClient, admin: str) -> None:
    assert (await client.get("/api/v1/work-items/nope/events/stream")).status_code == 404

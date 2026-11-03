"""A work item on the internal tracker, and the signals the fake Temporal received."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def make_work_item(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    tracker = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Arrival of Camille", "start": False}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


def signals_of() -> list[tuple[str, str, Any]]:
    from choregos_api.temporal import get_temporal

    return list(get_temporal().signals)  # type: ignore[attr-defined]

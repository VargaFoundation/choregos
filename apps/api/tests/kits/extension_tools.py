"""A run an installed extension's tools are called under."""

from __future__ import annotations

from typing import Any


async def run_extension_tool(project_id: str) -> tuple[str, dict[str, str]]:
    """A running run of the project and the headers of its run token."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        item = WorkItem(project_id=project_id, tracker_key="varga/x#11", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-greffon-1",
                work_item_id=item.id,
                project_id=project_id,
                stage_role="custom",
                status="running",
            )
        )
    token = mint_run_token(
        "run-greffon-1", project_slug="billing-api", work_item_key="varga/x#11", ttl_minutes=30
    )
    headers: dict[str, Any] = {"Authorization": f"Bearer {token}"}
    return "run-greffon-1", headers

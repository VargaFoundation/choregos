"""A run with a repository, and the headers of its run token (the git token tests)."""

from __future__ import annotations

import itertools
from typing import Any

_KEYS = itertools.count(1)


async def run_with_repository(project: dict[str, Any], url: str, **fields: Any) -> str:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        item = WorkItem(
            project_id=project["id"], tracker_key=f"ACME-{next(_KEYS)}", title="A fix", state="planned"
        )
        session.add(item)
        await session.flush()
        run = Run(
            work_item_id=item.id,
            project_id=project["id"],
            stage_role="implement",
            attempt=1,
            status="running",
            stage_input={"repo": {"url": url, "base_branch": "main", "work_branch": "choregos/acme-1"}},
            **fields,
        )
        session.add(run)
        await session.flush()
        return str(run.id)


def run_token_for(run_id: str) -> dict[str, str]:
    from choregos_api.config import get_settings
    from choregos_api.security import mint_run_token

    token = mint_run_token(
        run_id, project_slug="billing-api", work_item_key="ACME-1", ttl_minutes=5, settings=get_settings()
    )
    return {"Authorization": f"Bearer {token}"}

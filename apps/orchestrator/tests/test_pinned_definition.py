"""A work item keeps the workflow version it was born in (ADR 0031).

The interpreter used to reload "the project's active workflow" at every start, including at every
`continue_as_new`: publishing a new version while a work item waited made it change rules midway —
and, with two workflows in a project, the highest active version, whatever its name, won.
`load_context` now reads the work item's pin, and sets it when it is missing.
"""

from __future__ import annotations

from typing import Any

from .conftest import Fixture


async def _publish_a_v2(setup: Fixture, v1: str) -> str:
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        previous = await session.get(WorkflowDef, v1)
        assert previous is not None
        previous.is_active = False
        await session.flush()
        document: dict[str, Any] = dict(previous.json_doc)
        document["states"] = {
            **document["states"],
            "inbox": {**document["states"]["inbox"], "display": "Inbox v2"},
        }
        newer = WorkflowDef(
            project_id=setup.project_id,
            name=previous.name,
            version=previous.version + 1,
            source="platform",
            yaml=previous.yaml,
            json_doc=document,
            checksum="sha256:v2",
            is_active=True,
        )
        session.add(newer)
        await session.flush()
        return newer.id


async def test_a_work_item_keeps_the_version_it_was_born_in(setup: Fixture) -> None:
    from choregos_orchestrator.activities.interpretation import load_context

    context = {"project_id": setup.project_id, "work_item_id": setup.work_item_id}
    first = await load_context(context)
    v1 = first["workflow_def_id"]
    assert v1, "a work item from before the pin receives it at its first start"

    v2 = await _publish_a_v2(setup, v1)
    # A `continue_as_new`, a worker restart: the interpreter reloads its context.
    second = await load_context(context)
    assert second["workflow_def_id"] == v1 != v2
    assert second["workflow"]["states"]["inbox"]["display"] != "Inbox v2"


async def test_a_work_item_never_reads_another_projects_workflow(setup: Fixture) -> None:
    """A pin that designated another project's definition is ignored: the default serves."""
    from choregos_api.db.models import Organization, Project, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults
    from choregos_orchestrator.activities.base import work_item_workflow
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization))).scalars().first()
        assert org is not None
        other = Project(org_id=org.id, slug="other", name="Other", status="active", config={"slug": "other"})
        session.add(other)
        await session.flush()
        foreign, _ = await ensure_defaults(session, other)
        await session.flush()
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = foreign.id
        await session.flush()
        read_back = await work_item_workflow(session, item)
        own = (
            await session.execute(
                select(WorkflowDef).where(
                    WorkflowDef.project_id == setup.project_id, WorkflowDef.is_active.is_(True)
                )
            )
        ).scalar_one()
        assert read_back.metadata.name == own.name

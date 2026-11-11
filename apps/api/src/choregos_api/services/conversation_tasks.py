# SPDX-License-Identifier: Apache-2.0
"""The `run_task` tool of an assistant's conversation (ADR 0137 point 2, S63-13).

An installation whose manifest declares tasks with an `on_demand` trigger offers its conversations ONE tool,
`run_task(task)`, whose `task` is one of those names. Calling it starts the task the way the person's click
does (`assistant_tasks.run_now`): the work item is in the person's personal space, they are its initiator, the
session's identity applies and NO delegation is used, the budget check and the installation's readiness are
the click's. The agent is told the work item's key, never more: the result lands in the person's inbox.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import Role
from choregos_core.prompt_loop import LoopTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionRecord, AssistantInstallation, Membership, Organization, User
from ..errors import ApiError
from ..rbac import Principal
from . import assistant_tasks, assistants

TOOL = "run_task"


async def _on_demand_tasks(session: AsyncSession, row: AssistantInstallation) -> list[str]:
    document = await assistants.manifest(session, row)
    found: list[str] = []
    for declared in (document or {}).get("tasks") or []:
        if isinstance(declared, dict) and assistant_tasks.trigger_declaration(declared, "on_demand"):
            found.append(str(declared["name"]))
    return found


async def offered(session: AsyncSession, record: AgentSessionRecord) -> dict[str, LoopTool]:
    """`{run_task: its definition}` for an assistant conversation whose installation has on-demand tasks."""
    if not record.installation_id or not record.user_id:
        return {}
    row = await session.get(AssistantInstallation, record.installation_id)
    names = sorted(await _on_demand_tasks(session, row)) if row is not None else []
    if not names:
        return {}
    description = (
        "Start one of the assistant's tasks for the person now. It runs in the background and its result "
        "arrives in the person's inbox; nothing else is returned. Tasks: " + ", ".join(names)
    )
    parameters = {
        "type": "object",
        "properties": {"task": {"type": "string", "enum": names}},
        "required": ["task"],
        "additionalProperties": False,
    }
    return {TOOL: LoopTool(TOOL, description, parameters)}


async def _principal(session: AsyncSession, record: AgentSessionRecord) -> Principal | None:
    """The conversation's owner as the request would see them: their role in the organisation and groups."""
    user = await session.get(User, record.user_id) if record.user_id else None
    organisation = await session.get(Organization, record.org_id)
    if user is None or organisation is None:
        return None
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.org_id == organisation.id,
                Membership.project_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        return None
    return Principal(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name or "",
        org_roles={organisation.slug: Role(membership.role)},
        groups=list(user.groups or []),
    )


async def call(session: AsyncSession, record: AgentSessionRecord, arguments: dict[str, Any]) -> str:
    """Starts the task as the conversation's owner; what the agent reads back."""
    task = str(arguments.get("task") or "")
    principal = await _principal(session, record)
    row = await session.get(AssistantInstallation, record.installation_id) if record.installation_id else None
    organisation = await session.get(Organization, record.org_id)
    if principal is None or row is None or organisation is None:
        return "task not started: the person is no longer a member of the organisation"
    try:
        item, _ = await assistant_tasks.run_now(session, principal, organisation.slug, row.slug, task)
    except ApiError as refused:
        return f"task not started: {refused.detail}"
    return (
        f"The task `{task}` was started (work item {item.tracker_key}). Its result will arrive in the "
        "person's inbox; tell them so, and do not start it again for the same request."
    )

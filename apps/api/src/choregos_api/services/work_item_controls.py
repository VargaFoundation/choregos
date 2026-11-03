# SPDX-License-Identifier: Apache-2.0
"""Pause, resume and stop a work item with the rights of a principal (S47-14).

One place for the console's action and for a signed tracker comment (ADR 0102): the same permission,
the same signal, the same audit row."""

from __future__ import annotations

from typing import Literal

from choregos_contracts import Control
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Project, WorkItem
from ..errors import forbidden
from ..rbac import Permission, Principal
from ..temporal import deliver_control

ControlAction = Literal["pause", "resume", "stop"]


async def apply_control(
    session: AsyncSession,
    principal: Principal,
    project: Project,
    item: WorkItem,
    org_slug: str,
    action: ControlAction,
    reason: str | None,
    channel: str | None,
) -> None:
    """Signal the interpreter, update `item.paused` and audit `workitem.<action>`.

    Raises `forbidden` without ITEM_CONTROL, and re-raises `InterpreterNotRunningError` for the caller to
    word. `channel` (`tracker`) is recorded in the audit row when given."""
    if not principal.can(Permission.ITEM_CONTROL, org_slug, project.slug):
        raise forbidden("controlling a work item needs at least the developer role")
    control = Control(action=action, reason=reason, by=principal.email)
    await deliver_control(project.slug, item.tracker_key, control)
    item.paused = action == "pause"
    extra = {"channel": channel} if channel else {}
    await record(
        session,
        principal,
        f"workitem.{action}",
        org_id=project.org_id,
        target_type="work_item",
        target_id=item.id,
        reason=reason,
        **extra,
    )

# SPDX-License-Identifier: Apache-2.0
"""The reads of a task run's own agents (S63-13, S63-14, S63-16).

A task's workflow steps are ordinary runs of the personal space's work item (`documents.task`). When the
work item was started under a standing delegation (a scheduled or polled run), the agent's reads through the
broker are made as the person the need's identity says, judged by `assistant_delegations.guard_read` at each
call, and a write is refused (`write_under_delegation`: a task has no conversation to confirm it in).
Every read of a task run, delegated or not, is a line of the reads journal naming the work item (and the
delegation), never the content.

A connector the installation binds to no need (an agent reaching it by its own grant) keeps the run's own
identity and is not judged here: only the needs of the installation are the delegation's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from choregos_core import utcnow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    AssistantInstallation,
    AssistantRead,
    AssistantToolBinding,
    Delegation,
    Project,
    Run,
    WorkItem,
)
from . import assistant_delegations
from .assistant_delegations import DelegationRefused
from .assistant_memory import subjects_in
from .broker import BrokerTool
from .identity_exchange import Acting


@dataclass(frozen=True, slots=True)
class TaskRun:
    """The task a run belongs to: its work item, its installation and the delegation it started under."""

    item: WorkItem
    installation: AssistantInstallation
    delegation: Delegation | None


async def task_run_of(session: AsyncSession, run: Run) -> TaskRun | None:
    """`None` for a run that is not a step of a task's work item."""
    item = await session.get(WorkItem, run.work_item_id) if run.work_item_id else None
    task = (item.documents or {}).get("task") if item is not None else None
    if item is None or not isinstance(task, dict) or not task.get("installation_id"):
        return None
    installation = await session.get(AssistantInstallation, str(task["installation_id"]))
    if installation is None:
        return None
    delegation_id = task.get("delegation_id")
    delegation = await session.get(Delegation, str(delegation_id)) if delegation_id else None
    return TaskRun(item, installation, delegation)


async def _binding(
    session: AsyncSession, installation_id: str, connector_id: str
) -> AssistantToolBinding | None:
    return (
        (
            await session.execute(
                select(AssistantToolBinding).where(
                    AssistantToolBinding.installation_id == installation_id,
                    AssistantToolBinding.connector_id == connector_id,
                    AssistantToolBinding.enabled.is_(True),
                )
            )
        )
        .scalars()
        .first()
    )


async def acting_for_read(
    session: AsyncSession, run: Run, tool: BrokerTool
) -> tuple[Acting | None, TaskRun | None, str]:
    """`(who the read is, the task run, the need)` of a run's read. Under a delegation the read is judged and
    made as the person; `DelegationRefused` says which rule refused it. Not a task: `(None, None, "")`."""
    found = await task_run_of(session, run)
    if found is None:
        return None, None, ""
    binding = await _binding(session, found.installation.id, tool.connector.id)
    need = binding.need if binding is not None else tool.connector.name
    if found.delegation is None:  # on demand, or a task with no delegation: the run's own identity
        return None, found, need
    if binding is None:
        raise DelegationRefused(
            "outside_agreement", f"`{tool.connector.name}` is not a need of the agreement"
        )
    acting = await assistant_delegations.guard_read(
        session,
        found.delegation,
        found.installation,
        need=need,
        access=tool.access,
        binding=binding,
        operation=tool.operation.name,
    )
    return acting, found, need


async def refuse_write(session: AsyncSession, run: Run) -> None:
    """A write of a run started under a delegation is refused, whatever the tool."""
    found = await task_run_of(session, run)
    if found is not None and found.delegation is not None:
        raise DelegationRefused(
            "write_under_delegation",
            "a delegation is read-only and a task has no conversation to confirm a write in: "
            "the write is not made",
        )


async def journal_run_read(
    session: AsyncSession, found: TaskRun, need: str, tool: BrokerTool, result: dict[str, Any]
) -> None:
    """One line of the reads journal: the work item's key, the delegation, the subject the result carries."""
    subjects = subjects_in(result.get("structuredContent"))
    user_id = found.delegation.user_id if found.delegation is not None else None
    if user_id is None:
        project = await session.get(Project, found.item.project_id)
        user_id = project.owner_user_id if project is not None else None
    if user_id is None:
        return
    session.add(
        AssistantRead(
            org_id=found.installation.org_id,
            installation_id=found.installation.id,
            user_id=user_id,
            work_item_key=found.item.tracker_key,
            task=str(((found.item.documents or {}).get("task") or {}).get("task") or "") or None,
            need=need,
            operation=tool.operation.name,
            subject_type=subjects[0]["type"] if subjects else None,
            subject_key=subjects[0]["key"] if subjects else None,
            delegation_id=found.delegation.id if found.delegation is not None else None,
            at=utcnow(),
        )
    )

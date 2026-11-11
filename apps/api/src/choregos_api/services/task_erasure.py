# SPDX-License-Identifier: Apache-2.0
"""What a person's tasks and connected accounts leave behind, erased and purged (S63-16, ADR 0133 point 6).

Registered where the other holders register (`assistant_erasure.USER_ERASERS`, `assistant_retention.PURGERS`):

- `task_subscriptions`: the person's subscriptions to the installation's tasks (their `seen_keys` name
  objects).
- `task_artifacts`: the artifacts of the person's task work items of the installation, rows and inline
  content.
  The work item stays as the trace of the run (its key, state and cost), with no output. The bytes of a FILE
  artifact are in the object store, which has no delete: they stay there until the store's own lifecycle
  rule (outside this repository).
- `connected_accounts`: the person's accounts at the connectors the installation binds with `user_consent`,
  disconnected like the person's own click does (revoked at the source when it offers it, the sealed refresh
  token deleted). An account at a connector no need of this installation uses is left.
- by subject: the artifacts of the task runs whose reads (the reads journal, `work_item_key`) were about the
  erased subject.
- the purge: a task artifact older than the installation's retention (`policy.data.retention_days` of the
  manifest, 30 days when it names none) goes.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from typing import Any

from choregos_core.secrets import SecretNotFound
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    ArtifactRecord,
    AssistantInstallation,
    AssistantRead,
    AssistantToolBinding,
    ConnectedAccount,
    OrgConnector,
    Project,
    TaskSubscription,
    WorkItem,
)
from ..schemas.assistant_memory import MemorySubject
from ..secret_store import SecretStoreUnavailable, get_secret_store
from . import assistants
from .assistant_erasure import USER_ERASERS
from .assistant_retention import PURGERS
from .connected_accounts import refresh_secret_name, revoke_at_source
from .conversation_turns import DEFAULT_RETENTION_DAYS
from .identity_exchange import USER_CONSENT
from .personal_spaces import PERSONAL

BATCH = 500


def _task_of(item: WorkItem) -> dict[str, Any] | None:
    task = (item.documents or {}).get("task")
    return task if isinstance(task, dict) else None


async def _personal_items(
    session: AsyncSession, installation_id: str, *, user_id: str | None = None
) -> list[WorkItem]:
    query = select(WorkItem).join(Project, Project.id == WorkItem.project_id).where(Project.kind == PERSONAL)
    if user_id is not None:
        query = query.where(Project.owner_user_id == user_id)
    items = (await session.execute(query)).scalars()
    return [
        i for i in items if (t := _task_of(i)) is not None and t.get("installation_id") == installation_id
    ]


async def _delete_artifacts(session: AsyncSession, item_ids: list[str]) -> int:
    if not item_ids:
        return 0
    done = await session.execute(delete(ArtifactRecord).where(ArtifactRecord.work_item_id.in_(item_ids)))
    return int(done.rowcount or 0)  # type: ignore[attr-defined]


# -- by user -----------------------------------------------------------------------------------------------


async def _erase_subscriptions(
    session: AsyncSession, installation: AssistantInstallation, user_id: str
) -> int:
    done = await session.execute(
        delete(TaskSubscription).where(
            TaskSubscription.installation_id == installation.id, TaskSubscription.user_id == user_id
        )
    )
    return int(done.rowcount or 0)  # type: ignore[attr-defined]


async def _erase_artifacts(session: AsyncSession, installation: AssistantInstallation, user_id: str) -> int:
    items = await _personal_items(session, installation.id, user_id=user_id)
    return await _delete_artifacts(session, [i.id for i in items])


async def _erase_accounts(session: AsyncSession, installation: AssistantInstallation, user_id: str) -> int:
    connectors = (
        (
            await session.execute(
                select(OrgConnector)
                .join(AssistantToolBinding, AssistantToolBinding.connector_id == OrgConnector.id)
                .where(
                    AssistantToolBinding.installation_id == installation.id,
                    AssistantToolBinding.identity == USER_CONSENT,
                )
            )
        )
        .scalars()
        .all()
    )
    store = get_secret_store()
    removed = 0
    for connector in connectors:
        account = (
            await session.execute(
                select(ConnectedAccount).where(
                    ConnectedAccount.org_id == installation.org_id,
                    ConnectedAccount.user_id == user_id,
                    ConnectedAccount.connector == connector.name,
                )
            )
        ).scalar_one_or_none()
        if account is None:
            continue
        name = refresh_secret_name(user_id, connector.name)
        refresh: str | None
        try:
            refresh = (await store.get_many(session, installation.org_id, [name])).get(name)
        except (SecretNotFound, SecretStoreUnavailable):
            refresh = None
        with contextlib.suppress(SecretStoreUnavailable):  # the row goes in any case
            await store.delete(session, installation.org_id, name)
        await session.delete(account)
        await session.flush()
        removed += 1
        await revoke_at_source(session, installation.org_id, connector, refresh)
    return removed


USER_ERASERS["task_subscriptions"] = _erase_subscriptions
USER_ERASERS["task_artifacts"] = _erase_artifacts
USER_ERASERS["connected_accounts"] = _erase_accounts


# -- by subject --------------------------------------------------------------------------------------------


async def erase_subject_artifacts(
    session: AsyncSession, installation: AssistantInstallation, subject: MemorySubject
) -> int:
    """Deletes the artifacts of the task work items whose reads were about `subject`; how many rows went.
    Runs in the platform scope, once the caller's right is checked."""
    keys = {
        k
        for k in (
            await session.execute(
                select(AssistantRead.work_item_key).where(
                    AssistantRead.installation_id == installation.id,
                    AssistantRead.subject_type == subject.type,
                    AssistantRead.subject_key == subject.key,
                    AssistantRead.work_item_key.is_not(None),
                )
            )
        ).scalars()
        if k
    }
    if not keys:
        return 0
    items = await _personal_items(session, installation.id)
    return await _delete_artifacts(session, [i.id for i in items if i.tracker_key in keys])


# -- the purge ---------------------------------------------------------------------------------------------


async def retention_days_of(session: AsyncSession, installation: AssistantInstallation) -> int:
    document = await assistants.manifest(session, installation)
    declared = (((document or {}).get("policy") or {}).get("data") or {}).get("retention_days")
    if isinstance(declared, int) and not isinstance(declared, bool) and declared >= 1:
        return declared
    return DEFAULT_RETENTION_DAYS


async def purge_task_artifacts(session: AsyncSession, now: datetime) -> int:
    """Deletes the task artifacts older than their installation's retention."""
    deleted = 0
    for installation in (await session.execute(select(AssistantInstallation))).scalars().all():
        horizon = now - timedelta(days=await retention_days_of(session, installation))
        rows = (
            await session.execute(
                select(ArtifactRecord.id, WorkItem)
                .join(WorkItem, WorkItem.id == ArtifactRecord.work_item_id)
                .join(Project, Project.id == WorkItem.project_id)
                .where(
                    Project.kind == PERSONAL,
                    Project.org_id == installation.org_id,
                    ArtifactRecord.created_at < horizon,
                )
                .limit(BATCH)
            )
        ).all()
        old = [
            artifact_id
            for artifact_id, item in rows
            if (t := _task_of(item)) is not None and t.get("installation_id") == installation.id
        ]
        if old:
            await session.execute(delete(ArtifactRecord).where(ArtifactRecord.id.in_(old)))
            deleted += len(old)
    return deleted


PURGERS["task_artifacts"] = purge_task_artifacts

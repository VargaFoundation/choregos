# SPDX-License-Identifier: Apache-2.0
"""What a person's tasks and connected accounts leave behind, erased and purged (S63-16, ADR 0133 point 6).

Registered where the other holders register (`assistant_erasure.USER_ERASERS`, `assistant_retention.PURGERS`):

- `task_subscriptions`: the person's subscriptions to the installation's tasks (their `seen_keys` name
  objects).
- `task_artifacts`: the artifacts of the person's task work items of the installation, rows and inline
  content.
  The work item stays as the trace of the run (its key, state and cost), with no output. The bytes of a FILE
  artifact are deleted from the object store first (`ObjectStore.delete`, idempotent), then the row: when the
  store refuses, an erasure fails with a 502 and deletes nothing (the transaction rolls back, the person
  asks again: bytes already gone are not an error), and the purge keeps that row (and says so in the log)
  for its next pass. Never a deleted row with bytes left behind. A key still named by a row that stays
  (same name and content under two versions) is not deleted.
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

from choregos_adapters.storage import ObjectStoreError
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
from ..errors import upstream
from ..logging import get_logger
from ..object_store import object_store
from ..schemas.assistant_memory import MemorySubject
from ..schemas.assistant_reads import SubjectArtifact
from ..secret_store import SecretStoreUnavailable, get_secret_store
from . import assistants
from .assistant_erasure import USER_ERASERS
from .assistant_retention import PURGERS
from .connected_accounts import refresh_secret_name, revoke_at_source
from .conversation_turns import DEFAULT_RETENTION_DAYS
from .identity_exchange import USER_CONSENT
from .personal_spaces import PERSONAL

BATCH = 500
log = get_logger("choregos.assistants.task_erasure")


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


async def _delete_bytes(session: AsyncSession, rows: list[ArtifactRecord]) -> set[str]:
    """Deletes the object of each FILE artifact in `rows` that no row outside `rows` still names; the ids of
    the rows whose object could not be deleted (the store refused or did not answer)."""
    ids = {r.id for r in rows}
    keys = {r.storage_key for r in rows if r.storage_key}
    if not keys:
        return set()
    shared = set(
        (
            await session.execute(
                select(ArtifactRecord.storage_key).where(
                    ArtifactRecord.storage_key.in_(keys), ArtifactRecord.id.not_in(ids)
                )
            )
        ).scalars()
    )
    store = object_store()
    failed_keys: set[str] = set()
    for key in sorted(keys - shared):
        try:
            await store.delete(key)
        except ObjectStoreError as error:
            log.warning("assistants.task_artifacts.bytes_not_deleted", error=str(error)[:200])
            failed_keys.add(key)
    return {r.id for r in rows if r.storage_key in failed_keys}


async def _delete_artifacts(session: AsyncSession, item_ids: list[str]) -> int:
    """The bytes first, then the rows; all or nothing: one object the store refuses to delete fails the call
    before any row goes."""
    if not item_ids:
        return 0
    rows = list(
        (await session.execute(select(ArtifactRecord).where(ArtifactRecord.work_item_id.in_(item_ids))))
        .scalars()
        .all()
    )
    if await _delete_bytes(session, rows):
        raise upstream("object-store", "the files of the artifacts could not be deleted; nothing was erased")
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
        await revoke_at_source(session, installation.org_id, connector, account, refresh)
    return removed


USER_ERASERS["task_subscriptions"] = _erase_subscriptions
USER_ERASERS["task_artifacts"] = _erase_artifacts
USER_ERASERS["connected_accounts"] = _erase_accounts


# -- by subject --------------------------------------------------------------------------------------------


async def _items_about(
    session: AsyncSession, installation: AssistantInstallation, subject: MemorySubject
) -> list[WorkItem]:
    """The task work items whose reads (the reads journal, `work_item_key`) were about `subject`."""
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
        return []
    return [i for i in await _personal_items(session, installation.id) if i.tracker_key in keys]


async def erase_subject_artifacts(
    session: AsyncSession, installation: AssistantInstallation, subject: MemorySubject
) -> int:
    """Deletes the artifacts of the task work items whose reads were about `subject`; how many rows went.
    Runs in the platform scope, once the caller's right is checked."""
    items = await _items_about(session, installation, subject)
    return await _delete_artifacts(session, [i.id for i in items])


async def export_subject_artifacts(
    session: AsyncSession, installation: AssistantInstallation, subject: MemorySubject
) -> list[SubjectArtifact]:
    """What `erase_subject_artifacts` would delete, listed: each artifact with its inline content (a FILE
    artifact lists its storage key, its bytes are in the object store)."""
    items = {i.id: i for i in await _items_about(session, installation, subject)}
    if not items:
        return []
    rows = (
        (
            await session.execute(
                select(ArtifactRecord)
                .where(ArtifactRecord.work_item_id.in_(list(items)))
                .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        SubjectArtifact(
            id=a.id,
            work_item_key=items[a.work_item_id].tracker_key,
            task=str((_task_of(items[a.work_item_id]) or {}).get("task") or ""),
            name=a.name,
            version=a.version,
            media_type=a.media_type,
            content=a.content,
            storage_key=a.storage_key,
            created_at=a.created_at,
        )
        for a in rows
    ]


# -- the purge ---------------------------------------------------------------------------------------------


async def retention_days_of(session: AsyncSession, installation: AssistantInstallation) -> int:
    document = await assistants.manifest(session, installation)
    declared = (((document or {}).get("policy") or {}).get("data") or {}).get("retention_days")
    if isinstance(declared, int) and not isinstance(declared, bool) and declared >= 1:
        return declared
    return DEFAULT_RETENTION_DAYS


async def purge_task_artifacts(session: AsyncSession, now: datetime) -> int:
    """Deletes the task artifacts older than their installation's retention, their bytes first."""
    deleted = 0
    for installation in (await session.execute(select(AssistantInstallation))).scalars().all():
        horizon = now - timedelta(days=await retention_days_of(session, installation))
        rows = (
            await session.execute(
                select(ArtifactRecord, WorkItem)
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
            artifact
            for artifact, item in rows
            if (t := _task_of(item)) is not None and t.get("installation_id") == installation.id
        ]
        # A row whose object the store would not delete stays: the next pass retries it.
        kept = await _delete_bytes(session, old)
        gone = [a.id for a in old if a.id not in kept]
        if gone:
            await session.execute(delete(ArtifactRecord).where(ArtifactRecord.id.in_(gone)))
            deleted += len(gone)
    return deleted


PURGERS["task_artifacts"] = purge_task_artifacts

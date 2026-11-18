# SPDX-License-Identifier: Apache-2.0
"""Erasure and export of an assistant's personal data (S63-16, ADR 0133 point 6, ADR 0136).

- `by_user` (`DELETE /me/assistants/{org}/{slug}`): everything of the caller's in one installation: their
  conversations with the transcripts, their `user` and `user_subject` memories (stores, facts, profiles),
  and whatever the holders registered in `USER_ERASERS` hold of a person: standing delegations (S63-14), task
  subscriptions, task artifacts and connected accounts (`task_erasure`, S63-16). Always open to the person: it
  is their right, and the
  manifest cannot take it away.
- `by_subject` (`POST /orgs/{org}/assistants/{slug}/erasure`, `assistant:audit`, only when the manifest's
  `policy.data.erasure` lists `by_subject`): `export` returns every memory of the subject across every user of
  the installation and the conversations where it was resolved; `erase` deletes those memories and redacts
  those conversations (their messages and transcript events replaced by a marker, their metadata kept) or
  deletes them, and deletes the artifacts of the task runs whose reads (the reads journal) were about it. The
  rows of other people are under row-level security, so this runs in the platform scope
  once the caller's right is checked, and every statement names the installation.

Both are written in the audit log: who, which installation, how many rows. The subject's key is recorded as a
digest, never in clear. The reads journal keeps its lines (it is the accountability record) but loses the key
of an erased subject.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record as audit
from ..db.models import (
    AgentSessionEvent,
    AgentSessionRecord,
    AssistantInstallation,
    AssistantRead,
    MemoryFact,
    MemoryProfile,
    MemoryStore,
)
from ..db.session import in_platform_scope
from ..errors import conflict
from ..rbac import Principal
from ..schemas.assistant_memory import MemorySubject
from ..schemas.assistant_reads import (
    SubjectConversation,
    SubjectErasure,
    SubjectErasureRequest,
    SubjectMemory,
    SubjectMemoryFact,
    UserErasure,
)
from . import assistants
from .assistant_reads import ERASED
from .conversations import delete_conversation

REDACTED = "[redacted]"

#: What else holds a person's data in an installation, by name: `(session, installation, user_id)` removes
#: it and answers how many rows went. Delegations, task data and connected accounts register here.
UserEraser = Callable[[AsyncSession, AssistantInstallation, str], Awaitable[int]]
USER_ERASERS: dict[str, UserEraser] = {}


#: The prefix of the pseudonym a journal line carries once its person was erased.
ERASED_USER = "erased-user"


def digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# -- by user ---------------------------------------------------------------------------------------------


async def pseudonymise_reads(session: AsyncSession, installation: AssistantInstallation, user_id: str) -> int:
    """The person's lines of the reads journal keep what was read (need, operation, subject, when) but lose
    who: `user_id` becomes `erased-user:<random>`, one token for this erasure so that the lines still read as
    one person's, a value nothing links back (no hash of the id, which anyone with the user list could
    recompute). ADR 0133 point 6 says the journal is the evidence of what the installation read and lists
    no deletion of it: erasing the person's data removes the reference to the person, not the evidence."""
    token = f"{ERASED_USER}:{secrets.token_hex(8)}"
    done = await session.execute(
        update(AssistantRead)
        .where(AssistantRead.installation_id == installation.id, AssistantRead.user_id == user_id)
        .values(user_id=token)
        .execution_options(synchronize_session=False)
    )
    return int(done.rowcount or 0)  # type: ignore[attr-defined]


async def _delete_stores(session: AsyncSession, stores: list[MemoryStore]) -> tuple[int, int, int]:
    """Deletes the stores with their facts and profiles: `(stores, facts, profiles)`."""
    ids = [s.id for s in stores]
    if not ids:
        return 0, 0, 0
    facts = await session.execute(delete(MemoryFact).where(MemoryFact.memory_store_id.in_(ids)))
    profiles = await session.execute(delete(MemoryProfile).where(MemoryProfile.memory_store_id.in_(ids)))
    for store in stores:
        await session.delete(store)
    await session.flush()
    return len(ids), int(facts.rowcount or 0), int(profiles.rowcount or 0)  # type: ignore[attr-defined]


async def erase_user(
    session: AsyncSession, principal: Principal, org: str, installation: AssistantInstallation
) -> UserErasure:
    """Removes what belongs to the caller in the installation, and journals it."""
    from . import assistant_delegations, task_erasure  # noqa: F401 - they register their erasers on import

    user_id = principal.user_id
    await pseudonymise_reads(session, installation, user_id)
    conversations = list(
        (
            await session.execute(
                select(AgentSessionRecord).where(
                    AgentSessionRecord.installation_id == installation.id,
                    AgentSessionRecord.user_id == user_id,
                    AgentSessionRecord.kind == "conversation",
                )
            )
        ).scalars()
    )
    for conversation in conversations:
        await delete_conversation(session, conversation)
    stores = list(
        (
            await session.execute(
                select(MemoryStore).where(
                    MemoryStore.installation_id == installation.id, MemoryStore.user_id == user_id
                )
            )
        ).scalars()
    )
    n_stores, n_facts, n_profiles = await _delete_stores(session, stores)
    others = {name: await eraser(session, installation, user_id) for name, eraser in USER_ERASERS.items()}
    report = UserErasure(
        conversations=len(conversations),
        memory_stores=n_stores,
        memory_facts=n_facts,
        memory_profiles=n_profiles,
        others=others,
    )
    await audit(
        session,
        principal,
        "assistant.erasure.by_user",
        org_id=installation.org_id,
        target_type="assistant",
        target_id=installation.id,
        organisation=org,
        **report.model_dump(),
    )
    return report


# -- by subject ------------------------------------------------------------------------------------------


async def require_mode(session: AsyncSession, installation: AssistantInstallation, mode: str) -> None:
    """The manifest must list the mode in `policy.data.erasure`: a plugin that declared none offers none."""
    document = await assistants.manifest(session, installation)
    offered = (((document or {}).get("policy") or {}).get("data") or {}).get("erasure") or []
    if mode not in offered:
        raise conflict(f"the manifest of `{installation.slug}` does not offer erasure `{mode}`")


def _is_subject(entry: Any, subject: MemorySubject) -> bool:
    return isinstance(entry, dict) and entry.get("type") == subject.type and entry.get("key") == subject.key


async def _stores_of(
    session: AsyncSession, installation: AssistantInstallation, subject: MemorySubject
) -> list[MemoryStore]:
    rows = await session.execute(
        select(MemoryStore)
        .where(
            MemoryStore.installation_id == installation.id,
            MemoryStore.scope == "user_subject",
            MemoryStore.subject_type == subject.type,
            MemoryStore.subject_key == subject.key,
        )
        .order_by(MemoryStore.user_id, MemoryStore.slug)
    )
    return list(rows.scalars())


async def _conversations_of(
    session: AsyncSession, installation: AssistantInstallation, subject: MemorySubject
) -> list[AgentSessionRecord]:
    rows = await session.execute(
        select(AgentSessionRecord)
        .where(
            AgentSessionRecord.installation_id == installation.id,
            AgentSessionRecord.kind == "conversation",
        )
        .order_by(AgentSessionRecord.created_at, AgentSessionRecord.id)
    )
    # The column is JSON, read on both dialects: the filter is made here.
    return [r for r in rows.scalars() if any(_is_subject(e, subject) for e in r.resolved_subjects or [])]


def redact_message(message: dict[str, Any]) -> dict[str, Any]:
    """The message with what was said replaced: its content and the arguments of its tool calls."""
    out = dict(message)
    if out.get("content"):
        out["content"] = REDACTED
    if isinstance(out.get("tool_calls"), list):
        out["tool_calls"] = [
            {**c, "function": {**c["function"], "arguments": REDACTED}}
            if isinstance(c, dict) and isinstance(c.get("function"), dict)
            else c
            for c in out["tool_calls"]
        ]
    return out


async def _redact(session: AsyncSession, record: AgentSessionRecord, subject: MemorySubject) -> None:
    record.messages = [redact_message(m) for m in record.messages or []]
    record.archived_messages = [redact_message(m) for m in record.archived_messages or []]
    record.title = REDACTED if record.title else None
    record.context = {"redacted": True} if record.context else None
    record.resolved_subjects = [e for e in record.resolved_subjects or [] if not _is_subject(e, subject)]
    events = await session.execute(select(AgentSessionEvent).where(AgentSessionEvent.session_id == record.id))
    for event in events.scalars():
        # Numbers and flags (the turn, its cost) are metadata; every text or structure is content.
        event.payload = {
            k: v for k, v in (event.payload or {}).items() if isinstance(v, bool | int | float)
        } | {"redacted": True}


async def _export(
    session: AsyncSession, stores: list[MemoryStore], conversations: list[AgentSessionRecord]
) -> tuple[list[SubjectMemory], list[SubjectConversation]]:
    memories: list[SubjectMemory] = []
    for store in stores:
        facts = (
            await session.execute(
                select(MemoryFact)
                .where(MemoryFact.memory_store_id == store.id)
                .order_by(MemoryFact.created_at, MemoryFact.id)
            )
        ).scalars()
        profiles = (
            await session.execute(
                select(MemoryProfile)
                .where(MemoryProfile.memory_store_id == store.id)
                .order_by(MemoryProfile.version)
            )
        ).scalars()
        memories.append(
            SubjectMemory(
                user_id=store.user_id or "",
                space=store.slug,
                facts=[
                    SubjectMemoryFact(id=f.id, content=f.content, status=f.status, created_at=f.created_at)
                    for f in facts
                ],
                profiles=[
                    {"version": p.version, "status": p.status, "document": p.document} for p in profiles
                ],
            )
        )
    listed = [
        SubjectConversation(
            id=c.id,
            user_id=c.user_id,
            title=c.title,
            created_at=c.created_at,
            last_activity_at=c.last_activity_at,
        )
        for c in conversations
    ]
    return memories, listed


async def by_subject(
    session: AsyncSession,
    principal: Principal,
    org: str,
    installation: AssistantInstallation,
    request: SubjectErasureRequest,
) -> SubjectErasure:
    """Exports or erases one subject across every user of the installation; journaled with who did it."""
    await require_mode(session, installation, "by_subject")
    subject = request.subject
    report = SubjectErasure(mode=request.mode, subject=subject)
    async with in_platform_scope(session):
        stores = await _stores_of(session, installation, subject)
        conversations = await _conversations_of(session, installation, subject)
        if request.mode == "export":
            report.memories, report.conversations = await _export(session, stores, conversations)
            from .task_erasure import export_subject_artifacts

            report.task_artifacts = await export_subject_artifacts(session, installation, subject)
        else:
            report.memories_erased = (await _delete_stores(session, stores))[0]
            from .task_erasure import erase_subject_artifacts

            report.task_artifacts_erased = await erase_subject_artifacts(session, installation, subject)
            for conversation in conversations:
                if request.conversations == "delete":
                    await delete_conversation(session, conversation)
                    report.conversations_deleted += 1
                else:
                    await _redact(session, conversation, subject)
                    report.conversations_redacted += 1
            await session.execute(
                update(AssistantRead)
                .where(
                    AssistantRead.installation_id == installation.id,
                    AssistantRead.subject_type == subject.type,
                    AssistantRead.subject_key == subject.key,
                )
                .values(subject_key=ERASED)
            )
            await session.flush()
    await audit(
        session,
        principal,
        f"assistant.{request.mode}.by_subject",
        org_id=installation.org_id,
        target_type="assistant",
        target_id=installation.id,
        organisation=org,
        subject_type=subject.type,
        subject_digest=digest(subject.key),
        memories=len(stores) if request.mode == "export" else report.memories_erased,
        conversations=len(conversations),
        conversation_mode=request.conversations if request.mode == "erase" else None,
        task_artifacts=len(report.task_artifacts)
        if request.mode == "export"
        else report.task_artifacts_erased,
    )
    return report

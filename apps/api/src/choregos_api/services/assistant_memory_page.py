# SPDX-License-Identifier: Apache-2.0
"""The person's memory page of an assistant (ADR 0136 point 6): see, confirm or reject a proposal, correct,
pin, delete. A space is the person's: only `user` and `user_subject` spaces are on the page, and only the
rows of the person themself (on PostgreSQL a restrictive policy says the same to the database)."""

from __future__ import annotations

from typing import Any

from choregos_core import utcnow
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import MemoryFact, MemoryProfile, MemoryStore
from ..errors import conflict, not_found, unprocessable
from ..schemas.assistant_memory import (
    MemoryAction,
    MemoryExport,
    MemoryFactView,
    MemoryProfileVersion,
    MemorySpace,
    MemorySubject,
    MemorySubjectSpace,
)
from . import assistant_memory as memory


def _decl(spaces: memory.Spaces, space: str) -> dict[str, object]:
    found = spaces.decls.get(space)
    if found is None or found.get("scope") not in ("user", "user_subject"):
        raise not_found("Memory space", space)
    return found


async def _stores(
    session: AsyncSession, spaces: memory.Spaces, space: str, subject: str | None
) -> list[MemoryStore]:
    _decl(spaces, space)
    query = select(MemoryStore).where(
        MemoryStore.installation_id == spaces.installation.id,
        MemoryStore.user_id == spaces.user_id,
        MemoryStore.slug == space,
    )
    if subject is not None:
        query = query.where(MemoryStore.subject_key == subject)
    return list((await session.execute(query.order_by(MemoryStore.subject_key))).scalars())


async def view(
    session: AsyncSession, spaces: memory.Spaces, space: str, subject: str | None = None
) -> MemorySpace:
    decl = _decl(spaces, space)
    stores = await _stores(session, spaces, space, subject)
    if subject is not None and not stores:
        raise not_found("Memory", subject)
    return await space_of(session, decl, space, stores)


async def space_of(
    session: AsyncSession, decl: dict[str, Any], space: str, stores: list[MemoryStore]
) -> MemorySpace:
    """The page of one space: what each of its stores holds (shared with the installation owners' page)."""
    subjects: list[MemorySubjectSpace] = []
    for store in stores:
        facts = await memory.facts_of(session, store, statuses=("active", "proposed"))
        subjects.append(
            MemorySubjectSpace(
                subject=MemorySubject(type=store.subject_type, key=store.subject_key)
                if store.subject_type and store.subject_key
                else None,
                facts=[MemoryFactView(**memory.fact_view(f)) for f in facts],
                profiles=[
                    MemoryProfileVersion(
                        version=p.version,
                        status=p.status,
                        document=p.document,
                        updated_by=p.updated_by,
                        created_at=p.created_at,
                    )
                    for p in await memory.profiles_of(session, store)
                ],
            )
        )
    return MemorySpace(
        space=space,
        scope=decl["scope"],
        kind=decl["kind"],
        writes=str(decl["writes"]),
        subjects=subjects,
    )


async def fact_of(session: AsyncSession, stores: list[MemoryStore], fact_id: str | None) -> MemoryFact:
    fact = await session.get(MemoryFact, fact_id) if fact_id else None
    if fact is None or fact.memory_store_id not in {s.id for s in stores}:
        raise not_found("Memory fact", fact_id or "")
    return fact


async def act(
    session: AsyncSession, spaces: memory.Spaces, space: str, subject: str | None, body: MemoryAction
) -> MemorySpace:
    decl = _decl(spaces, space)
    if body.action == "add":
        await add(session, spaces, decl, subject, body)
        await session.flush()
        return await view(session, spaces, space, subject)
    stores = await _stores(session, spaces, space, subject)
    if not stores:
        raise not_found("Memory", subject or space)
    if body.action in ("confirm", "reject") and body.version is not None:
        await decide_profile(session, stores, body)
    elif body.action in ("confirm", "reject"):
        fact = await fact_of(session, stores, body.fact_id)
        if fact.status != "proposed":
            raise conflict(f"the fact is {fact.status}: only a proposal is confirmed or rejected")
        fact.status = "active" if body.action == "confirm" else "rejected"
    elif body.action in ("pin", "unpin"):
        fact = await fact_of(session, stores, body.fact_id)
        fact.provenance = {**(fact.provenance or {}), "pinned": body.action == "pin"}
    elif decl["kind"] == "facts":
        if not body.content:
            raise unprocessable("correcting a fact needs its new `content`")
        fact = await fact_of(session, stores, body.fact_id)
        fact.content = body.content
        fact.provenance = {**(fact.provenance or {}), "corrected_by_user": True}
    else:
        await _write_profile(session, stores[0], decl, body)
    await session.flush()
    return await view(session, spaces, space, subject)


async def add(
    session: AsyncSession,
    spaces: memory.Spaces,
    decl: dict[str, Any],
    subject: str | None,
    body: MemoryAction,
) -> None:
    """The person writes in their own space what the agent may not (`user_only`) or may only propose: a fact,
    or a profile document, active at once. The store of the space is created by the first write."""
    key: tuple[str, str] | None = None
    if decl["scope"] == "user_subject":
        try:
            key = memory.well_formed(decl, {"type": (decl.get("subject") or {}).get("type"), "key": subject})
        except memory.MemoryRefused as refusal:
            raise unprocessable(refusal.message) from refusal
    elif subject is not None:
        raise unprocessable(f"`{decl['name']}` is not about a subject")
    store = await memory.ensure_store(session, spaces, decl, key)
    if decl["kind"] == "facts":
        if not body.content:
            raise unprocessable("adding a fact needs its `content`")
        session.add(
            MemoryFact(
                org_id=store.org_id,
                project_id=None,
                memory_store_id=store.id,
                kind="note",
                subject=key[1] if key else str(decl["name"]),
                content=body.content,
                source="user",
                status="active",
                valid_from=utcnow(),
                provenance={"reason": "written by the person", "pinned": False},
                proposed_by="user",
                paths=[],
            )
        )
        await session.flush()
        await memory.facts_of(session, store)  # applies `max_facts` and `retention_days`
        return
    await _write_profile(session, store, decl, body)


async def export(session: AsyncSession, spaces: memory.Spaces) -> MemoryExport:
    """Everything remembered of the person in this assistant, for them to keep or to carry elsewhere."""
    found = [
        await view(session, spaces, name)
        for name, decl in sorted(spaces.decls.items())
        if decl.get("scope") in ("user", "user_subject")
    ]
    return MemoryExport(
        installation=spaces.installation.slug,
        organisation=spaces.organisation.slug,
        exported_at=utcnow(),
        spaces=found,
    )


async def decide_profile(session: AsyncSession, stores: list[MemoryStore], body: MemoryAction) -> None:
    row = (
        await session.execute(
            select(MemoryProfile).where(
                MemoryProfile.memory_store_id.in_([s.id for s in stores]),
                MemoryProfile.version == body.version,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Profile version", str(body.version))
    if row.status != "proposed":
        raise conflict(f"the version is {row.status}: only a proposal is confirmed or rejected")
    if body.action == "reject":
        row.status = "rejected"
        return
    for other in await memory.profiles_of(session, next(s for s in stores if s.id == row.memory_store_id)):
        if other.status == "active":
            other.status = "superseded"
    row.status = "active"


async def _write_profile(
    session: AsyncSession, store: MemoryStore, decl: dict[str, Any], body: MemoryAction
) -> None:
    """The person's own correction of the profile: a new active version (the person may write what the
    agent may only propose)."""
    if body.document is None:
        raise unprocessable("correcting a profile needs the new `document`")
    try:
        # The manifest as installed now, not the schema the store was created with (an upgrade moved it).
        memory.sync_store(store, decl)
        memory.validate_profile(store.schema, body.document)
    except memory.MemoryRefused as refusal:
        raise unprocessable(refusal.message) from refusal
    versions = await memory.profiles_of(session, store)
    for old in versions:
        if old.status == "active":
            old.status = "superseded"
    session.add(
        MemoryProfile(
            org_id=store.org_id,
            memory_store_id=store.id,
            version=(versions[0].version + 1) if versions else 1,
            document=body.document,
            status="active",
            updated_by="user",
        )
    )


async def forget(
    session: AsyncSession, spaces: memory.Spaces, space: str, subject: str | None, fact_id: str | None
) -> None:
    """Deletes one fact, or (no `fact_id`) every store of the space for the person or the subject."""
    stores = await _stores(session, spaces, space, subject)
    if fact_id is not None:
        await session.delete(await fact_of(session, stores, fact_id))
        return
    if not stores:
        raise not_found("Memory", subject or space)
    for store in stores:
        await session.execute(delete(MemoryFact).where(MemoryFact.memory_store_id == store.id))
        await session.execute(delete(MemoryProfile).where(MemoryProfile.memory_store_id == store.id))
        await session.delete(store)

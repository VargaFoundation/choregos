# SPDX-License-Identifier: Apache-2.0
"""Artifacts: every output a step leaves is typed, stored and traced (S31-01, ADR 0049).

An inline output (a specification, a list of profiles) keeps its value in the database; a file (a
slide deck, a video) keeps its bytes in the object store. Either way the row records the type, the
media type, the size, the sha256 of the bytes served on download, and the run that produced it —
its stage, its attempt, its registry agent and version.

A version is a distinct content: the same bytes produced again (a replayed activity, a retry that
rewrites the same specification) make no new version, so recording is idempotent (ADR 0008).

Every read recomputes the digest. Bytes that no longer match — an object rewritten behind the
platform's back, a value edited in the database — are refused, never served.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog
from choregos_adapters.storage import ObjectStore
from choregos_contracts import (
    ARTIFACT_NAME_PATTERN,
    ARTIFACT_TYPES,
    Artifact,
    ArtifactProducer,
    artifact_digest,
    artifact_type,
    inline_bytes,
    type_of_value,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ArtifactRecord, Run, WorkItem
from ..errors import not_found

log = structlog.get_logger(__name__)

_NAME = re.compile(ARTIFACT_NAME_PATTERN)
#: Two producers racing for the same next version: the unique constraint decides, and the loser
#: tries the following number. Three rounds are plenty for outputs written once per run.
_ROUNDS = 3


class ArtifactCorruptedError(RuntimeError):
    """The stored bytes no longer hash to the recorded digest: the artifact is not served."""


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def storage_key(org_id: str, work_item_id: str, name: str, digest: str) -> str:
    """Content-addressed: writing the same bytes twice writes the same object."""
    return f"orgs/{org_id}/work-items/{work_item_id}/artifacts/{name}/{digest.removeprefix('sha256:')}"


async def latest(session: AsyncSession, work_item_id: str, name: str) -> ArtifactRecord | None:
    return (
        await session.execute(
            select(ArtifactRecord)
            .where(ArtifactRecord.work_item_id == work_item_id, ArtifactRecord.name == name)
            .order_by(ArtifactRecord.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _record(  # noqa: PLR0913 - one row, field by field, keyword-only
    session: AsyncSession,
    *,
    org_id: str,
    item: WorkItem,
    run: Run,
    name: str,
    type_: str,
    digest: str,
    size: int,
    content: Any = None,
    key: str | None = None,
    url: str | None = None,
) -> ArtifactRecord:
    entry = artifact_type(type_)
    for _ in range(_ROUNDS):
        previous = await latest(session, item.id, name)
        if previous is not None and previous.digest == digest:
            return previous
        row = ArtifactRecord(
            org_id=org_id,
            work_item_id=item.id,
            name=name,
            version=(previous.version + 1) if previous else 1,
            type=type_,
            kind=str(entry.kind),
            media_type=entry.media_type,
            digest=digest,
            size=size,
            content=content,
            storage_key=key,
            storage_url=url,
            run_id=run.id,
            stage=run.stage_role,
            attempt=run.attempt,
            agent_slug=run.agent_slug,
            agent_version=run.agent_version,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            continue
        return row
    raise RuntimeError(f"artifact `{name}`: could not take a version after {_ROUNDS} attempts")


def _parsed(raw: Any) -> Any:
    """Text that parses as JSON is the document it spells; anything else is kept as it is."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


async def record_inline_outputs(
    session: AsyncSession,
    *,
    org_id: str,
    item: WorkItem,
    run: Run,
    outputs: dict[str, Any],
    types: dict[str, str] | None = None,
) -> list[ArtifactRecord]:
    """One artifact revision per present output of a step, typed by its declaration or its value.

    A declared inline type (`markdown`, `json`) is kept; a list declared `markdown` is JSON. Text
    declared `json` that parses is the document it spells, not a JSON string: a model often answers
    JSON as text, `output_schema` validates it parsed, and the next step reads `<name>.json` (S38-13)."""
    recorded: list[ArtifactRecord] = []
    for name, raw in outputs.items():
        if not _present(raw):
            continue
        if not _NAME.fullmatch(name):
            log.warning("artifact name refused", work_item=item.tracker_key, name=name)
            continue
        declared = (types or {}).get(name)
        value = _parsed(raw) if declared == "json" else raw
        type_ = declared if declared in {"markdown", "json"} else type_of_value(value)
        if type_ == "markdown" and not isinstance(value, str):
            type_ = "json"
        data = inline_bytes(value)
        recorded.append(
            await _record(
                session,
                org_id=org_id,
                item=item,
                run=run,
                name=name,
                type_=type_,
                digest=artifact_digest(data),
                size=len(data),
                content=value,
            )
        )
    return recorded


async def store_file(
    session: AsyncSession,
    store: ObjectStore,
    *,
    org_id: str,
    item: WorkItem,
    run: Run,
    name: str,
    type_: str,
    data: bytes,
) -> ArtifactRecord:
    """Writes a file artifact to the object store, then records it. Same bytes, same version."""
    if not _NAME.fullmatch(name):
        raise ValueError(f"artifact name `{name}` refused: {ARTIFACT_NAME_PATTERN}")
    entry = artifact_type(type_)
    digest = artifact_digest(data)
    previous = await latest(session, item.id, name)
    if previous is not None and previous.digest == digest:
        return previous
    key = storage_key(org_id, item.id, name, digest)
    url = await store.put(key, data, entry.media_type)
    return await _record(
        session,
        org_id=org_id,
        item=item,
        run=run,
        name=name,
        type_=type_,
        digest=digest,
        size=len(data),
        key=key,
        url=url,
    )


async def read_content(store: ObjectStore, row: ArtifactRecord) -> bytes:
    """The bytes of a revision, after checking they still hash to its digest."""
    data = await store.get(row.storage_key) if row.storage_key else inline_bytes(row.content)
    found = artifact_digest(data)
    if found != row.digest:
        log.error(
            "artifact digest mismatch",
            work_item_id=row.work_item_id,
            name=row.name,
            version=row.version,
            recorded=row.digest,
            found=found,
        )
        raise ArtifactCorruptedError(
            f"artifact `{row.name}` v{row.version}: its content no longer matches its digest "
            f"(recorded {row.digest}, found {found}) — it is not served"
        )
    return data


def to_contract(row: ArtifactRecord) -> Artifact:
    return Artifact.model_validate(
        {
            "name": row.name,
            "type": row.type,
            "kind": row.kind,
            "media_type": row.media_type,
            "version": row.version,
            "digest": row.digest,
            "size": row.size,
            "produced_by": ArtifactProducer(
                run_id=row.run_id,
                stage=row.stage,
                attempt=row.attempt,
                agent=row.agent_slug,
                agent_version=row.agent_version,
            ),
            "storage_url": row.storage_url,
            "created_at": row.created_at,
        }
    )


async def artifacts_of(session: AsyncSession, work_item_id: str) -> list[ArtifactRecord]:
    """Every revision of every artifact of a work item, by name then version."""
    return list(
        (
            await session.execute(
                select(ArtifactRecord)
                .where(ArtifactRecord.work_item_id == work_item_id)
                .order_by(ArtifactRecord.name, ArtifactRecord.version)
            )
        )
        .scalars()
        .all()
    )


def filename_of(row: ArtifactRecord) -> str:
    """The file name a revision is downloaded and mounted under: its name, with its type's extension."""
    extension = ARTIFACT_TYPES[row.type].extension if row.type in ARTIFACT_TYPES else ""
    if not extension or row.name.lower().endswith(f".{extension}"):
        return row.name
    return f"{row.name}.{extension}"


async def revision(session: AsyncSession, work_item_id: str, ref: str) -> ArtifactRecord:
    """`name` is the latest revision, `name@3` the third; an artifact name never holds `@`."""
    name, _, version = ref.partition("@")
    query = select(ArtifactRecord).where(
        ArtifactRecord.work_item_id == work_item_id, ArtifactRecord.name == name
    )
    if version:
        if not version.isdigit():
            raise not_found("Artifact", ref)
        query = query.where(ArtifactRecord.version == int(version))
    row = (await session.execute(query.order_by(ArtifactRecord.version.desc()).limit(1))).scalar_one_or_none()
    if row is None:
        raise not_found("Artifact", ref)
    return row

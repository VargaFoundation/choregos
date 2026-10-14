# SPDX-License-Identifier: Apache-2.0
"""What a run is served of a knowledge base (ADR 0051, S33-02).

When the run is prepared, `pin()` resolves each base its agent names — it must exist and be served
to the run's project — and pins its ready documents by id, with the digest of the files they make.
The runner then fetches exactly those documents (`GET /internal/runs/{id}/knowledge`), recomputes
the digest, and lays them read-only under `.choregos/knowledge/<slug>/`, with an `INDEX.md`.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from choregos_contracts import KnowledgeRef, knowledge_digest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from ..db.models import KnowledgeBase, KnowledgeDocument
from .access import served_to_project

INDEX = "INDEX.md"
_UNSAFE = re.compile(r"[^a-z0-9._-]+")


class KnowledgeRefusedError(RuntimeError):
    """A base the agent names cannot be served to this run; the message says why."""


def file_name(filename: str, taken: set[str]) -> str:
    """A markdown file name for a document: its own stem, made safe, unique in the base."""
    stem = _UNSAFE.sub("-", PurePosixPath(filename).stem.lower()).strip("-.") or "document"
    name, n = f"{stem[:80]}.md", 2
    while name in taken or name == INDEX.lower():
        name, n = f"{stem[:80]}-{n}.md", n + 1
    taken.add(name)
    return name


def files_of(documents: list[KnowledgeDocument]) -> list[dict[str, str]]:
    """The files a base lays down, in a stable order (file name, then id)."""
    taken: set[str] = set()
    return [
        {
            "path": file_name(document.filename, taken),
            "title": document.title,
            "filename": document.filename,
            "markdown": document.markdown or "",
        }
        for document in sorted(documents, key=lambda d: (d.filename, d.id))
    ]


async def _ready(session: AsyncSession, base: KnowledgeBase, ids: list[str] | None = None) -> list[Any]:
    query = (
        select(KnowledgeDocument)
        .options(undefer(KnowledgeDocument.markdown))
        .where(KnowledgeDocument.knowledge_base_id == base.id, KnowledgeDocument.status == "ready")
    )
    if ids is not None:
        query = query.where(KnowledgeDocument.id.in_(ids))
    return list((await session.execute(query)).scalars())


async def _base(session: AsyncSession, org_id: str, slug: str) -> KnowledgeBase | None:
    return (
        await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.org_id == org_id, KnowledgeBase.slug == slug)
        )
    ).scalar_one_or_none()


async def pin(session: AsyncSession, org_id: str, project_slug: str, slugs: list[str]) -> list[KnowledgeRef]:
    """The knowledge bases an agent names, resolved for one run of one project."""
    refs: list[KnowledgeRef] = []
    for slug in dict.fromkeys(slugs):
        base = await _base(session, org_id, slug)
        if base is None:
            raise KnowledgeRefusedError(f"the knowledge base `{slug}` does not exist")
        if not served_to_project(base, project_slug):
            raise KnowledgeRefusedError(
                f"the knowledge base `{slug}` is not served to the project `{project_slug}` "
                f"(scope `{base.scope}`, status `{base.status}`)"
            )
        if base.serving_mode == "files":
            documents = await _ready(session, base)
            refs.append(
                KnowledgeRef(
                    slug=slug,
                    name=base.name,
                    mode="files",
                    documents=sorted(d.id for d in documents),
                    digest=knowledge_digest(files_of(documents)),
                )
            )
        else:
            refs.append(KnowledgeRef(slug=slug, name=base.name, mode="index"))
    return refs


async def deliver(
    session: AsyncSession, org_id: str, project_slug: str, ref: dict[str, Any]
) -> dict[str, Any]:
    """The pinned documents of one `files` base, as the runner lays them down."""
    base = await _base(session, org_id, str(ref["slug"]))
    if base is None or not served_to_project(base, project_slug):
        raise KnowledgeRefusedError(f"the knowledge base `{ref['slug']}` is no longer served to this run")
    files = files_of(await _ready(session, base, list(ref.get("documents") or [])))
    return {"slug": base.slug, "name": base.name, "digest": knowledge_digest(files), "documents": files}

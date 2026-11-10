# SPDX-License-Identifier: Apache-2.0
"""Knowledge bases (ADR 0051) and the memory stores."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class MemoryStore(Base, PkMixin, TimestampMixin):
    """A named memory (S40-11, ADR 0075): `global` (the organisation's, curated by an administrator,
    with a reach) or `project` (a project's, curated by its owners, read by its runs only).

    The implicit memories of a project (`shared`, `own`) are never rows.
    """

    __tablename__ = "memory_stores"
    __table_args__ = (
        UniqueConstraint("org_id", "owner_key", "slug", name="uq_memory_stores_owner_slug"),
        CheckConstraint("(scope = 'project') = (project_id IS NOT NULL)", name="ck_memory_stores_scope"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: global | project
    scope: Mapped[str] = mapped_column(String(16))
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: `org`, or the project's id: a unique key without NULLs.
    owner_key: Mapped[str] = mapped_column(String(36))
    #: For a global memory, whose runs may read it: `org` (every project) or `projects` (listed).
    reach: Mapped[str | None] = mapped_column(String(16), nullable=True)
    projects: Mapped[list[str]] = mapped_column(Json, default=list)
    #: active | archived
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class MemoryFact(Base, PkMixin, TimestampMixin):
    """Lexical fallback: memory stored in Choregos when Ecphoria is not deployed.

    `embedding` is a `Json`, not a vector: the similarity is a dot product between
    bags of words, computed in Python. The connector used to be called `pgvector`, which promised
    something else."""

    __tablename__ = "memory_facts"
    __table_args__ = (
        Index("ix_memory_facts_project_subject", "project_id", "subject"),
        CheckConstraint(
            "project_id IS NOT NULL OR memory_store_id IS NOT NULL", name="ck_memory_facts_has_a_home"
        ),
    )

    #: The organisation the fact belongs to (S40-11): what the row-level security keys on, since a
    #: global memory's facts belong to no project.
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    #: `None` for the facts of a global memory (they carry `memory_store_id` only).
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    #: The named memory the fact is in (S40-11); `None` for a project's shared memory and for an
    #: agent's own memory.
    memory_store_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_stores.id", ondelete="CASCADE"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    proposed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding: Mapped[list[str] | None] = mapped_column(Json, nullable=True)
    paths: Mapped[list[str]] = mapped_column(Json, default=list)
    #: The agent whose own memory this is (S32-02, `memory: agent`); `None` for the project's
    #: shared memory. An agent's facts are read by that agent only, in this project only.
    agent_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class KnowledgeBase(Base, PkMixin, TimestampMixin):
    """A curated reference corpus of the organisation, read-only for agents (ADR 0051).

    Not the memory: memory holds facts learned from work (proposed, then accepted); a knowledge
    base holds documents a human chose. `scope` says who may use it: `org` (every project),
    `projects` (the listed project slugs) or `groups` (the listed IdP groups, humans only).
    """

    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_knowledge_bases_org_slug"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: org | projects | groups
    scope: Mapped[str] = mapped_column(String(16), default="org")
    projects: Mapped[list[str]] = mapped_column(Json, default=list)
    groups: Mapped[list[str]] = mapped_column(Json, default=list)
    #: files (the converted corpus mounted in the workspace) | index (hybrid search)
    serving_mode: Mapped[str] = mapped_column(String(16), default="files")
    #: active | archived
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: questions and the documents (file names) expected to answer them (S33-03)
    eval_set: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    #: the last run of the eval set: recall@5, per question, when, in which search mode
    eval_result: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: the last distillation into a principles skill (S33-05): status, skill, version, dropped, error
    distillation: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: the project this base was created from (S39-14): its owners curate it and widen its reach
    owner_project: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: people read it only through a grant (or as an administrator or owning-project owner); a run
    #: is served it only on behalf of a person who reads it (S42-11e, ADR 0097)
    restricted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: the assistant installation this base belongs to (S63-05, ADR 0138): the copy of a seed made at
    #: install, offered to that installation's conversations and to no other. S63-06 sets it; null for
    #: every base made through the knowledge routes. Deleting the installation keeps the base, unlinked.
    installation_id: Mapped[str | None] = mapped_column(
        ForeignKey("assistant_installations.id", ondelete="SET NULL"), nullable=True, index=True
    )


class KnowledgeGrant(Base, PkMixin, TimestampMixin):
    """A role on one knowledge base given to a person, a group or a project (S42-11, ADR 0081).

    `subject_type`: `user` (`subject` is a user id, or an e-mail kept as typed for a person with no
    account yet), `group` (an IdP group name) or `project` (a project slug). `role`: viewer, editor
    or owner. A grant gives people a role; it never serves a run.
    """

    __tablename__ = "knowledge_grants"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "subject_type", "subject", name="uq_knowledge_grants_subject"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    subject_type: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class KnowledgeDocument(Base, PkMixin, TimestampMixin):
    """One document of a knowledge base: its source bytes, kept for a re-conversion, and the
    markdown they became. `status`: pending → converting → ready | failed."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "digest", name="uq_knowledge_documents_kb_digest"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    #: where it came from: `upload` today, a connector tomorrow (S33-04)
    source: Mapped[str] = mapped_column(String(32), default="upload")
    filename: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    #: sha256 of the source bytes: the same file twice is one document
    digest: Mapped[str] = mapped_column(String(80))
    #: the uploaded bytes, never loaded with a listing
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: what a `ready` conversion left out, one sentence each (S42-09e): a spreadsheet cut at its row cap
    warnings: Mapped[list[str]] = mapped_column(Json, default=list, server_default="[]")
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: the connector source it was synced from (S33-04); none for an upload
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: its identity at the source (a Graph item id, a URL, a path) and its version there
    source_ref: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class KnowledgeSource(Base, PkMixin, TimestampMixin):
    """Where a knowledge base's documents are synced from (S33-04, ADR 0051): `sharepoint` (a site or
    drive through the organisation's `documents` connector, delta sync), `web` (one allow-listed
    site, crawled) or `git` (a repository's documentation). A document deleted at the source leaves
    the base and its index at the next sync."""

    __tablename__ = "knowledge_sources"

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    #: the org connector (`documents` capability) holding the credentials, for `sharepoint`
    connector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: where the next sync starts: a Graph deltaLink, a commit
    cursor: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    #: interval | cron | manual (S39-19): what decides when the loop syncs
    refresh_mode: Mapped[str] = mapped_column(String(8), default="interval", server_default="interval")
    #: five fields, minute resolution, read in `refresh_timezone`; set in `cron` mode
    refresh_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    refresh_timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    #: when the source syncs next (an estimate: the worker may be down); null = due, or manual
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: set by "sync now": a request later than `last_synced_at` makes the next pass sync
    sync_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: idle | syncing | failed
    status: Mapped[str] = mapped_column(String(16), default="idle")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: what the last sync did: added, updated, deleted, unchanged, skipped
    last_summary: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class KnowledgeDistillationPart(Base, PkMixin, TimestampMixin):
    """One model call of a distillation in passes, kept by the digest of what it read (S42-10c, ADR 0008).

    A part (`part_index >= 0`) holds the principles one slice of the corpus yielded; the merge
    (`part_index == -1`) holds the final list. The digest covers the model, the instructions and the
    text read, so a replayed activity, or a new distillation after one document changed, finds the
    answer instead of paying for it again.
    """

    __tablename__ = "knowledge_distillation_parts"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "digest", name="uq_knowledge_distillation_parts"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    digest: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(200))
    part_index: Mapped[int] = mapped_column(Integer)
    documents: Mapped[list[str]] = mapped_column(Json, default=list)
    characters: Mapped[int] = mapped_column(Integer, default=0)
    answer: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    #: done | failed
    status: Mapped[str] = mapped_column(String(16), default="done")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeChunk(Base, PkMixin, TimestampMixin):
    """A contextual chunk of a document of an `index` knowledge base (S33-03, ADR 0051).

    `text` is what is ranked: the document title and heading path, then the chunk. On PostgreSQL the
    migration adds what the ORM does not describe: a generated `search_tsv` (full-text, GIN) and,
    when pgvector is installed, `embedding_vec`. `embedding` (JSON) holds the vector only where
    there is no pgvector column to hold it (SQLite, development and tests).
    """

    __tablename__ = "knowledge_chunks"

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[list[str]] = mapped_column(Json, default=list)
    body: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Json, nullable=True, deferred=True)

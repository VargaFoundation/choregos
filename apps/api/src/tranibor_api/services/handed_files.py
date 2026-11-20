# SPDX-License-Identifier: Apache-2.0
"""Files a person hands to a work item (ADR 0197): the checks, the names and the two producers.

A handed file is an input artifact, stored as an A2A file is: the object store first, then the rows,
so that a store that fails leaves no row. Its producer is `person:<user id>`, beside `a2a:<work item id>`.
The API stores bytes; it never converts them, and never places them in a prompt.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tranibor_adapters.storage import ObjectStore, ObjectStoreError
from tranibor_contracts import (
    A2A_MAX_FILE_BYTES_DEFAULT,
    ARTIFACT_NAME_PATTERN,
    RESERVED_ARTIFACT_NAMES,
    A2aConfig,
    Artifact,
    Workflow,
    WorkflowAttachments,
    type_of_filename,
)

from ..audit import record
from ..db.models import ArtifactRecord, Project, WorkItem
from ..errors import ApiError, conflict, unprocessable, upstream
from ..knowledge.formats import FORMATS, DocumentRefusedError, accept, safe_filename
from ..rbac import Principal
from .artifacts import (
    StoredInput,
    handed,
    handed_artifacts,
    latest,
    put_input_file,
    record_input_file,
    to_contract,
)

log = structlog.get_logger(__name__)

_NAME = re.compile(ARTIFACT_NAME_PATTERN)
_OUTSIDE = re.compile(r"[^A-Za-z0-9_.-]")
_RUNS = re.compile(r"-{2,}")
_MAX_NAME = 128
#: What a multipart envelope adds around the files, at most, before the size cap refuses the request.
ENVELOPE = 64 * 1024
AUDIT_ACTION = "work_item.file.attached"


@dataclass(frozen=True, slots=True)
class HandedFile:
    """What a person sent: the file name their client gave, and the bytes."""

    filename: str
    data: bytes


def slug(filename: str) -> str:
    """A file's artifact name (ADR 0197 §4): ASCII folding, every character outside `[A-Za-z0-9_.-]`
    replaced by `-`, runs of `-` collapsed, the extension kept, 128 characters at most. Empty: refused."""
    # The base name only, and not cut at 255 characters like `safe_filename`: the extension must survive.
    base = PurePosixPath(filename.replace("\\", "/")).name.strip()
    folded = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    name = _RUNS.sub("-", _OUTSIDE.sub("-", folded)).lstrip("-._")
    if len(name) > _MAX_NAME:
        extension = PurePosixPath(name).suffix
        keep = _MAX_NAME - len(extension) if len(extension) < _MAX_NAME else _MAX_NAME
        name = name[:keep] + (extension if keep < _MAX_NAME else "")
    return name if _NAME.fullmatch(name) else ""


def file_limit(project: Project) -> int:
    """The project's limit for a handed file, whatever the door (`a2a.max_file_bytes`, ADR 0197 §3)."""
    try:
        return A2aConfig.model_validate((project.config or {}).get("a2a") or {}).max_file_bytes
    except ValidationError:
        return A2A_MAX_FILE_BYTES_DEFAULT


def attachments_of(workflow: Workflow) -> WorkflowAttachments | None:
    return workflow.metadata.attachments


def is_terminal(workflow: Workflow, item: WorkItem) -> bool:
    declared = workflow.states.get(item.state)
    return declared is not None and declared.terminal


def _too_large(name: str, limit: int) -> ApiError:
    return ApiError(
        413,
        "Payload too large",
        f"file `{name}` is {limit} bytes at most for this project (`a2a.max_file_bytes`)",
    )


@dataclass(frozen=True, slots=True)
class _Checked:
    name: str
    type: str
    data: bytes


def _check(sent: HandedFile, attachments: WorkflowAttachments, limit: int) -> _Checked:
    shown = safe_filename(sent.filename) or sent.filename
    if len(sent.data) > limit:
        raise _too_large(shown, limit)
    name = slug(sent.filename)
    if not name:
        raise unprocessable(f"file name `{shown}` leaves no usable name once slugged")
    if name in RESERVED_ARTIFACT_NAMES:
        raise unprocessable(f"file name `{shown}` is reserved by the platform")
    type_ = type_of_filename(name)
    if type_ not in attachments.accept:
        raise unprocessable(
            f"`{shown}` is a `{type_}` file; this workflow accepts: {', '.join(attachments.accept)}"
        )
    _check_bytes(shown, name, type_, sent.data)
    return _Checked(name=name, type=type_, data=sent.data)


#: Types whose bytes are text: not empty, and no NUL byte where a text never has one.
_TEXT_TYPES = frozenset({"csv", "text", "markdown", "json", "mermaid", "sarif", "html", "svg"})
#: The first bytes of the image types a person may hand.
_MAGIC = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
}


def _check_bytes(shown: str, name: str, type_: str, data: bytes) -> None:
    """The bytes are what the type says (ADR 0197 §3): a knowledge-base upload's checks for the formats
    it knows (magic bytes, an Office archive's bombs), a text check for text, the magic bytes of an image.
    A type the platform cannot check is refused rather than stored unchecked."""
    if PurePosixPath(name.lower()).suffix in FORMATS:
        try:
            accept(name, data)
        except DocumentRefusedError as refusal:
            raise unprocessable(str(refusal)) from refusal
        return
    if not data:
        raise unprocessable(f"`{shown}` is empty")
    if type_ in _TEXT_TYPES:
        if b"\0" in data[:8192]:
            raise unprocessable(f"`{shown}` is binary, not {type_} text")
        return
    if type_ in _MAGIC:
        if not data.startswith(_MAGIC[type_]):
            raise unprocessable(f"`{shown}` does not start like a {type_} file")
        return
    raise unprocessable(f"`{shown}`: the platform does not check `{type_}` files, so it does not take them")


async def hand_files(
    session: AsyncSession,
    store: ObjectStore,
    principal: Principal,
    project: Project,
    item: WorkItem,
    workflow: Workflow,
    files: list[HandedFile],
) -> list[ArtifactRecord]:
    """Checks every file, stores them all, then records one input artifact per file and one audit entry.

    Nothing is written to the database before every file is in the object store; a file refused
    refuses the request.
    """
    attachments = attachments_of(workflow)
    if attachments is None:
        raise unprocessable(
            f"workflow `{workflow.metadata.name}` takes no file from a person (no `metadata.attachments`)"
        )
    if is_terminal(workflow, item):
        raise conflict(f"work item `{item.tracker_key}` is {item.state}: it takes no more files")
    if not files:
        raise unprocessable("expected at least one file in the multipart field `files`")
    limit = file_limit(project)
    checked = [_check(one, attachments, limit) for one in files]
    names = [one.name for one in checked]
    clashing = sorted({name for name in names if names.count(name) > 1})
    if clashing:
        raise unprocessable(
            "several files reach the same name once slugged, none was kept: " + ", ".join(clashing)
        )
    known = {row.name for row in await handed_artifacts(session, item.id)}
    for name in names:
        existing = await latest(session, item.id, name)
        if existing is not None and handed(existing) is None:
            raise unprocessable(f"`{name}` is already the output of a step: rename the file")
    after = known | set(names)
    if len(after) > attachments.max:
        raise unprocessable(
            f"{len(after)} distinct files would be handed, this workflow takes {attachments.max} at most"
        )
    stored: list[StoredInput] = []
    for one in checked:
        try:
            stored.append(
                await put_input_file(
                    store,
                    org_id=project.org_id,
                    work_item_id=item.id,
                    name=one.name,
                    type_=one.type,
                    data=one.data,
                )
            )
        except ObjectStoreError as failed:
            log.error("work_item.file_not_stored", name=one.name, error=str(failed))
            raise upstream(
                "object-store", f"file `{one.name}` could not be stored; nothing was recorded"
            ) from failed
    rows: list[ArtifactRecord] = []
    for kept in stored:
        row = await record_input_file(
            session, org_id=project.org_id, item=item, stored=kept, person=principal.user_id
        )
        await record(
            session,
            principal,
            AUDIT_ACTION,
            org_id=project.org_id,
            target_type="work_item",
            target_id=item.id,
            name=row.name,
            version=row.version,
            digest=row.digest,
            size=row.size,
        )
        rows.append(row)
    return rows


def contracts_of(rows: list[ArtifactRecord]) -> list[Artifact]:
    return [to_contract(row) for row in rows]


async def handed_count(session: AsyncSession, item: WorkItem) -> int:
    """How many distinct names a person or an application handed to the work item."""
    return len(await handed_artifacts(session, item.id))


def refuse_start_without_files(workflow: Workflow) -> None:
    """Creating a work item that starts at once is refused (422) when its workflow requires files."""
    attachments = attachments_of(workflow)
    if attachments is not None and attachments.min >= 1:
        raise unprocessable(
            f"workflow `{workflow.metadata.name}` requires at least {attachments.min} file(s) "
            "(`metadata.attachments.min`): create the work item with `start: false`, hand its files "
            "with `POST /work-items/{id}/files`, then send the action `mark_agent_ready`",
            [{"loc": ["start"], "msg": "a start waits for the files the workflow requires"}],
        )


async def refuse_start_below_min(session: AsyncSession, workflow: Workflow, item: WorkItem) -> None:
    """`mark_agent_ready` is refused (409) while fewer than `min` files were handed."""
    attachments = attachments_of(workflow)
    if attachments is None or attachments.min < 1:
        return
    count = await handed_count(session, item)
    if count < attachments.min:
        raise conflict(
            f"workflow `{workflow.metadata.name}` requires at least {attachments.min} handed file(s) "
            f"and this work item has {count}: hand the rest with `POST /work-items/{{id}}/files`"
        )

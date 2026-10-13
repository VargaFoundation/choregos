# SPDX-License-Identifier: Apache-2.0
"""Artifacts: what a step leaves behind, typed, stored and traced (ADR 0049).

An artifact is one revision of one named output of a work item: a specification, a list of
profiles, a SARIF report, a slide deck. It says what it is (`type`, `kind`, `media_type`), where its
bytes are (`storage_url`, or inline in the platform's database), what those bytes hash to
(`digest`, checked again on every read) and which run produced it (`produced_by`).

The vocabulary of types lives here so the workflow validator, the runner and the API agree on it.
Generating the files is not the platform's job: an office file comes from a skill run in the
sandbox, an image or a video from a model behind the gateway or an MCP connector. The platform only
types, stores, hashes and traces what an agent wrote.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from .workflow import Strict

#: An artifact name: what a transition declares in `outputs:`. It becomes a file name in the
#: downstream workspace (`.choregos/inputs/<name>.<ext>`), hence no slash and no leading dot.
ARTIFACT_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"


class ArtifactKind(StrEnum):
    """How the platform handles an artifact: inline text or data, or a file in the object store."""

    MARKDOWN = "markdown"
    JSON = "json"
    DIAGRAM = "diagram"
    REPORT = "report"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class ArtifactType:
    """One entry of the type vocabulary: its kind, its media type, its file extension."""

    kind: ArtifactKind
    media_type: str
    extension: str

    @property
    def inline(self) -> bool:
        """A declared output of an inline type is a value in the result's `outputs`, kept in the
        database; any other type is a file the agent writes in `.choregos/outputs/`, kept in the
        object store."""
        return self.kind in {ArtifactKind.MARKDOWN, ArtifactKind.JSON}


#: The types a transition may declare for an output (`output_types:`) and the runner recognises
#: in `.choregos/outputs/`. `markdown` and `json` are inline; every other type is a file.
ARTIFACT_TYPES: dict[str, ArtifactType] = {
    "markdown": ArtifactType(ArtifactKind.MARKDOWN, "text/markdown", "md"),
    "json": ArtifactType(ArtifactKind.JSON, "application/json", "json"),
    "mermaid": ArtifactType(ArtifactKind.DIAGRAM, "text/vnd.mermaid", "mmd"),
    "sarif": ArtifactType(ArtifactKind.REPORT, "application/sarif+json", "sarif"),
    "text": ArtifactType(ArtifactKind.FILE, "text/plain", "txt"),
    "csv": ArtifactType(ArtifactKind.FILE, "text/csv", "csv"),
    "html": ArtifactType(ArtifactKind.FILE, "text/html", "html"),
    "pdf": ArtifactType(ArtifactKind.FILE, "application/pdf", "pdf"),
    "docx": ArtifactType(
        ArtifactKind.FILE,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "xlsx": ArtifactType(
        ArtifactKind.FILE, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    ),
    "pptx": ArtifactType(
        ArtifactKind.FILE,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
    ),
    "png": ArtifactType(ArtifactKind.FILE, "image/png", "png"),
    "jpeg": ArtifactType(ArtifactKind.FILE, "image/jpeg", "jpg"),
    "gif": ArtifactType(ArtifactKind.FILE, "image/gif", "gif"),
    "webp": ArtifactType(ArtifactKind.FILE, "image/webp", "webp"),
    "svg": ArtifactType(ArtifactKind.FILE, "image/svg+xml", "svg"),
    "mp4": ArtifactType(ArtifactKind.FILE, "video/mp4", "mp4"),
    "webm": ArtifactType(ArtifactKind.FILE, "video/webm", "webm"),
    "file": ArtifactType(ArtifactKind.FILE, "application/octet-stream", ""),
}

#: Extensions that name the same type as another spelling.
_EXTENSION_ALIASES = {"jpg": "jpeg", "md": "markdown", "mmd": "mermaid", "txt": "text", "htm": "html"}


def artifact_type(name: str) -> ArtifactType:
    """The entry of a declared type; `KeyError` names the types that exist."""
    try:
        return ARTIFACT_TYPES[name]
    except KeyError:
        raise KeyError(
            f"unknown artifact type `{name}`: one of {', '.join(sorted(ARTIFACT_TYPES))}"
        ) from None


def type_of_value(value: Any) -> str:
    """The type of an inline output: text is markdown, anything else is JSON."""
    return "markdown" if isinstance(value, str) else "json"


def type_of_filename(filename: str) -> str:
    """The type a file's extension names; `file` when the extension is unknown."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    extension = _EXTENSION_ALIASES.get(extension, extension)
    return extension if extension in ARTIFACT_TYPES else "file"


def inline_bytes(value: Any) -> bytes:
    """The bytes an inline artifact is served as, and hashed over: text as UTF-8, data as JSON.

    JSON is canonical (sorted keys, no spaces) so the same value always hashes the same.
    """
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def artifact_digest(data: bytes) -> str:
    """`sha256:<hex>` of the bytes: what anyone can recompute with `sha256sum` on a download."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


class ArtifactProducer(Strict):
    """The run that produced an artifact revision, and the agent behind it."""

    run_id: str
    stage: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    #: The registry agent (ADR 0033) and its version; empty for a playbook.
    agent: str | None = None
    agent_version: int | None = Field(default=None, ge=1)


class Artifact(Strict):
    """One revision of a named output of a work item (ADR 0049)."""

    name: str = Field(pattern=ARTIFACT_NAME_PATTERN)
    #: A key of `ARTIFACT_TYPES`: what the transition declared, or what the value or file was.
    type: str
    kind: ArtifactKind
    media_type: str
    #: 1 for the first revision; a new content makes the next one.
    version: int = Field(ge=1)
    #: `sha256:<hex>` of the bytes served on download; checked again on every read.
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size: int = Field(ge=0)
    produced_by: ArtifactProducer
    #: Where the bytes are in the object store; `null` for an inline artifact kept in the database.
    storage_url: str | None = None
    created_at: datetime | None = None

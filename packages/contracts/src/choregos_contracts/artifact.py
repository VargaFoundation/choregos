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
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from .workflow import ArtifactLevel, ArtifactScale, ArtifactShow, ArtifactSpec, Strict

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
    DIFF = "diff"
    VALUE = "value"


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
        return self.kind in _INLINE_KINDS


_INLINE_KINDS = {ArtifactKind.MARKDOWN, ArtifactKind.JSON, ArtifactKind.DIFF, ArtifactKind.VALUE}

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
    "diff": ArtifactType(ArtifactKind.DIFF, "text/x-diff", "diff"),
    "level": ArtifactType(ArtifactKind.VALUE, "text/plain", "txt"),
    "metric": ArtifactType(ArtifactKind.VALUE, "application/json", "json"),
    "keyvalue": ArtifactType(ArtifactKind.JSON, "application/json", "json"),
}

#: The artifact the platform records after a step, from the SCM's comparison of the pushed branch
#: (ADR 0129 §7). Measured, never declared: no output may take this name.
DIFF_ARTIFACT = "diff"
RESERVED_ARTIFACT_NAMES = frozenset({DIFF_ARTIFACT})

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


class ArtifactDiffStat(Strict):
    """What a `diff` revision changes, so a one-line summary needs no read of its content."""

    files: int = Field(ge=0)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    #: The diff was too large and was cut, file by file (ADR 0129 §8).
    truncated: bool = False
    #: The shapes of secret found: the patch was withheld, and only the files are kept. Never a value.
    withheld: list[str] = Field(default_factory=list)


class ArtifactDisplay(Strict):
    """How the console shows a revision (ADR 0129). Snapshotted on it, outside its digest."""

    #: The type it is drawn as; a revision older than ADR 0129 can be drawn as more than it is stored.
    #: `null` only in a stage's input: a place given to an output whose type its value will settle.
    type: str | None = None
    show: ArtifactShow = "tab"
    title: str | None = None
    diff: ArtifactDiffStat | None = None
    levels: list[ArtifactLevel] | None = None
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    better: Literal["higher", "lower"] | None = None


#: The scales the platform ships (ADR 0129 §3). A size only ranks; a risk and a verdict judge.
SCALES: dict[ArtifactScale, list[ArtifactLevel]] = {
    "risk": [
        ArtifactLevel(value="low", tone="ok"),
        ArtifactLevel(value="medium", tone="warn"),
        ArtifactLevel(value="high", tone="danger"),
    ],
    "size": [
        ArtifactLevel(value="S"),
        ArtifactLevel(value="M"),
        ArtifactLevel(value="L"),
        ArtifactLevel(value="XL"),
    ],
    "verdict": [
        ArtifactLevel(value="approve", tone="ok", title="Approved"),
        ArtifactLevel(value="comment", tone="info", title="Comment"),
        ArtifactLevel(value="request_changes", tone="warn", title="Changes requested"),
    ],
}

#: What the platform knows of an output by its name alone: every workflow, every older revision.
WELL_KNOWN_ARTIFACTS: dict[str, ArtifactSpec] = {
    "size": ArtifactSpec(type="level", scale="size", show="facts", title="Size"),
    "risk": ArtifactSpec(type="level", scale="risk", show="facts", title="Risk"),
    "verdict": ArtifactSpec(type="level", scale="verdict", title="Verdict"),
    "confidence": ArtifactSpec(type="metric", min=0, max=1, better="higher", title="Confidence"),
    DIFF_ARTIFACT: ArtifactSpec(type="diff", title="Changes"),
}

#: What fits on one line of a ticket's facts card (ADR 0129 §5).
FACT_TYPES = frozenset({"level", "metric", "diff"})
#: A typed value: what the list carries, and what is checked.
VALUE_TYPES = frozenset({"level", "metric"})
#: The types that say what a value is, as opposed to the format of a document.
CHECKED_TYPES = VALUE_TYPES | {"keyvalue"}
#: At most this many names in a ticket's facts card.
MAX_FACTS = 8


def resolve_display(name: str, spec: ArtifactSpec | None = None) -> ArtifactDisplay | None:
    """What a declaration comes to, over what the platform knows of the name; `None` when neither speaks."""
    known = WELL_KNOWN_ARTIFACTS.get(name)
    if spec is None and known is None:
        return None
    declared = spec.model_dump(exclude_none=True) if spec is not None else {}
    merged = ArtifactSpec.model_validate(
        {**(known.model_dump(exclude_none=True) if known else {}), **declared}
    )
    if merged.scale is not None and merged.levels is None:
        merged = merged.model_copy(update={"levels": SCALES[merged.scale]})
    return ArtifactDisplay(
        type=merged.type,
        show=merged.show or "tab",
        title=merged.title,
        levels=merged.levels,
        unit=merged.unit,
        min=merged.min,
        max=merged.max,
        better=merged.better,
    )


def _level(display: ArtifactDisplay, raw: Any) -> str:
    if not isinstance(raw, str | int | float) or isinstance(raw, bool):
        raise ValueError("a level is one word")
    allowed = display.levels or []
    text = str(raw).strip().lower()
    for level in allowed:
        if level.value.lower() == text:
            return level.value
    raise ValueError(f"must be one of {', '.join(f'`{level.value}`' for level in allowed)}")


def _metric(display: ArtifactDisplay, raw: Any) -> float | int:
    if isinstance(raw, bool):
        raise ValueError("a metric is a number")
    number: float | int
    if isinstance(raw, int | float):
        number = raw
    elif isinstance(raw, str):
        text = raw.strip().removesuffix("%").strip()
        if display.unit and text.endswith(display.unit):
            text = text.removesuffix(display.unit).strip()
        try:
            number = int(text) if text.lstrip("+-").isdigit() else float(text)
        except ValueError:
            raise ValueError("a metric is a number") from None
    else:
        raise ValueError("a metric is a number")
    try:
        finite = math.isfinite(number)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("a metric is a finite number")
    if isinstance(number, float) and number.is_integer():
        # PostgreSQL's `jsonb` hands `1e+22` back without its exponent: an integer hashes the same both ways.
        number = int(number)
    if display.min is not None and number < display.min:
        raise ValueError(f"must be at least {display.min:g}")
    if display.max is not None and number > display.max:
        raise ValueError(f"must be at most {display.max:g}")
    return number


def _keyvalue(raw: Any) -> dict[str, Any]:
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            raise ValueError("a key/value output is a JSON object") from None
    if not isinstance(value, dict) or not value:
        raise ValueError("a key/value output is a JSON object")
    if any(isinstance(v, dict | list) for v in value.values()):
        raise ValueError("a key/value output is flat: its values are text, numbers or booleans")
    return value


def coerce_value(display: ArtifactDisplay, raw: Any) -> Any:
    """The value a typed output comes to (`"0.9"`, `"87%"`, a level's case); `ValueError` says why not."""
    if display.type == "level":
        return _level(display, raw)
    if display.type == "metric":
        return _metric(display, raw)
    if display.type == "keyvalue":
        return _keyvalue(raw)
    return raw


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
    #: How the console shows it (ADR 0129); `null` when nothing is said beyond its type.
    display: ArtifactDisplay | None = None
    #: A `level` or a `metric`, once its digest checked (ADR 0129 §11); the rest is read from its content.
    value: str | int | float | None = None

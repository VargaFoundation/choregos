# SPDX-License-Identifier: Apache-2.0
"""What a knowledge base accepts, decided on the bytes before any parser opens them.

The extension says what the file claims to be; the first bytes must agree. An Office file is a zip:
its central directory is read (cheap, nothing is inflated) to refuse a zip bomb before the converter
would inflate it.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

OOXML = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
TEXT = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
}
FORMATS: dict[str, str] = {**OOXML, ".pdf": "application/pdf", **TEXT}

#: What an Office archive may inflate to, in all; past it, it is a bomb or not a document.
MAX_INFLATED = 256 * 1024 * 1024
MAX_ENTRIES = 10_000


class DocumentRefusedError(ValueError):
    """The upload is not a document this knowledge base accepts; the message says why."""


@dataclass(frozen=True)
class Accepted:
    extension: str
    mime: str
    filename: str


def safe_filename(filename: str) -> str:
    """The file's own name, without the folders a browser or a client may prefix."""
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    return name[:255]


def accept(filename: str, data: bytes) -> Accepted:
    """Checks the name and the bytes agree on a format we convert; raises `DocumentRefusedError`."""
    name = safe_filename(filename)
    extension = PurePosixPath(name.lower()).suffix
    if not name or extension not in FORMATS:
        raise DocumentRefusedError(
            f"`{name or filename}`: unsupported format; accepted: {', '.join(sorted(FORMATS))}"
        )
    if not data:
        raise DocumentRefusedError(f"`{name}` is empty")
    if extension in OOXML:
        _check_office(name, data)
    elif extension == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise DocumentRefusedError(f"`{name}` does not start like a PDF")
    elif b"\0" in data[:8192]:
        raise DocumentRefusedError(f"`{name}` is binary, not text")
    return Accepted(extension=extension, mime=FORMATS[extension], filename=name)


def _check_office(name: str, data: bytes) -> None:
    if not data.startswith(b"PK\x03\x04"):
        raise DocumentRefusedError(f"`{name}` is not an Office document (not a zip archive)")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise DocumentRefusedError(f"`{name}` is a damaged archive: {exc}") from exc
    if len(entries) > MAX_ENTRIES:
        raise DocumentRefusedError(f"`{name}` holds {len(entries)} parts, more than {MAX_ENTRIES}")
    inflated = sum(entry.file_size for entry in entries)
    if inflated > MAX_INFLATED:
        raise DocumentRefusedError(
            f"`{name}` would inflate to {inflated // (1024 * 1024)} MiB, more than "
            f"{MAX_INFLATED // (1024 * 1024)} MiB"
        )
    if "[Content_Types].xml" not in {entry.filename for entry in entries}:
        raise DocumentRefusedError(f"`{name}` is a zip archive, not an Office document")

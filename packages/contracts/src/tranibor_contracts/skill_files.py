# SPDX-License-Identifier: Apache-2.0
"""How a skill file travels as a string: text as UTF-8, an inert binary as base64 (ADR 0050).

A skill's files are a `dict[str, str]` everywhere (API, storage, runner). The rule that keeps that
shape without any ambiguity is decided by the **extension alone**: a path whose extension is in
`INERT_BINARY_EXTENSIONS` (fonts, images) ALWAYS carries standard base64 of its bytes, and every
other path carries UTF-8 text. A text file can therefore never be mistaken for a binary one, and
the other way round. SVG is text, not binary.

The API checks a binary once, on import (`check_inert_binary`: its magic bytes match its extension,
it fits `MAX_BINARY_BYTES`); the runner decodes with the same function before it writes bytes.
"""

from __future__ import annotations

import base64
import binascii
import posixpath

#: The cap on the DECODED size of one binary file.
MAX_BINARY_BYTES = 512 * 1024

#: extension -> the prefixes a file of that type may start with (the magic bytes).
INERT_BINARY_EXTENSIONS: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),  # plus `WEBP` at offset 8, see `_magic_ok`
    ".ico": (b"\x00\x00\x01\x00",),
    ".ttf": (b"\x00\x01\x00\x00", b"true"),
    ".otf": (b"OTTO",),
    ".woff": (b"wOFF",),
    ".woff2": (b"wOF2",),
}


def is_inert_binary_path(path: str) -> bool:
    """Whether a file of this name is carried as base64 (decided by its extension only)."""
    return posixpath.splitext(path.lower())[1] in INERT_BINARY_EXTENSIONS


def _magic_ok(extension: str, data: bytes) -> bool:
    if not data.startswith(INERT_BINARY_EXTENSIONS[extension]):
        return False
    return extension != ".webp" or data[8:12] == b"WEBP"


def check_inert_binary(path: str, data: bytes) -> None:
    """Raises `ValueError` unless `data` is an acceptable binary for `path`: within the cap, and
    starting with the magic bytes of its extension."""
    extension = posixpath.splitext(path.lower())[1]
    if extension not in INERT_BINARY_EXTENSIONS:
        raise ValueError(f"`{path}` is not an inert binary type")
    if len(data) > MAX_BINARY_BYTES:
        raise ValueError(
            f"`{path}` is {len(data)} bytes: a binary file is {MAX_BINARY_BYTES // 1024} KiB at most"
        )
    if not _magic_ok(extension, data):
        raise ValueError(f"`{path}` does not look like a `{extension}` file (its first bytes disagree)")


def encode_binary(data: bytes) -> str:
    """The string a binary file is stored as."""
    return base64.b64encode(data).decode("ascii")


def skill_file_bytes(path: str, value: str) -> bytes:
    """The bytes of a stored skill file: decoded base64 for an inert-binary extension, UTF-8 text
    otherwise. Raises `ValueError` on base64 that is not strictly valid."""
    if not is_inert_binary_path(path):
        return value.encode("utf-8")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as error:
        raise ValueError(f"`{path}` is not valid base64") from error

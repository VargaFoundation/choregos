# SPDX-License-Identifier: Apache-2.0
"""One file to Markdown, in a child process with a timeout (ADR 0198 point 3).

The file comes on stdin, `python -m tranibor_procurement.convert <kind>` answers one JSON object on
stdout. The parent kills the child past its timeout and gives it a bare environment: a hostile
document reaches a parser, never a credential. PDF, DOCX and PPTX go through MarkItDown, a workbook
through openpyxl (cached values, a row cap per sheet), CSV and text are read as UTF-8 with a fallback
to Latin-1. There is no OCR: a PDF without a text layer is unreadable, and so are an encrypted file,
a timeout and any converter error, each with a reason word.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

DEFAULT_TIMEOUT_S: Final = 30
ROWS_PER_SHEET: Final = 2000
#: A PDF whose text holds fewer non-blank characters per page than this has no text layer.
MIN_CHARS_PER_PAGE: Final = 20

#: The reason words of an unreadable file.
ENCRYPTED: Final = "encrypted"
TIMEOUT: Final = "timeout"
NO_TEXT_LAYER: Final = "no_text_layer"
CONVERTER_ERROR: Final = "converter_error"
UNSUPPORTED: Final = "unsupported_type"
TYPE_MISMATCH: Final = "type_mismatch"
EMPTY: Final = "empty"

_OFFICE = {".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx"}
_TEXT = {".csv": "csv", ".tsv": "csv", ".txt": "text", ".md": "text"}
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_ROOTS = {"word/": "docx", "ppt/": "pptx", "xl/": "xlsx"}
_BLANKS = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Converted:
    kind: str
    markdown: str
    pages: int | None


@dataclass(frozen=True)
class Unreadable:
    reason: str
    detail: str = ""


def kind_of(name: str, head: bytes, names: Sequence[str] = ()) -> str | Unreadable:
    """The kind of a file from its magic bytes and its extension; `names` are a zip's entries."""
    ext = Path(name).suffix.lower()
    if head.startswith(b"%PDF-"):
        found = "pdf"
    elif head.startswith(b"PK\x03\x04"):
        found = next((k for root, k in _ZIP_ROOTS.items() if any(n.startswith(root) for n in names)), "zip")
    elif head.startswith(_OLE):
        # Office files protected by a password are OLE containers; the legacy formats are too.
        return Unreadable(ENCRYPTED if ext in _OFFICE else UNSUPPORTED, "OLE container")
    else:
        found = _TEXT.get(ext, "")
        if found:
            return found
        if ext == ".pdf" or ext in _OFFICE:
            return Unreadable(TYPE_MISMATCH, f"`{ext}` without the signature of its format")
        return Unreadable(UNSUPPORTED, f"`{ext or name}`")
    expected = "pdf" if ext == ".pdf" else _OFFICE.get(ext)
    if expected != found:
        return Unreadable(TYPE_MISMATCH, f"`{ext}` holds a {found} file")
    return found


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _cell(value: Any, number_format: str) -> str:
    import datetime as dt

    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time(0) else value.isoformat(sep=" ")
    if isinstance(value, dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, int | float):
        if "%" in (number_format or ""):
            return f"{value * 100:g}%"
        return str(int(value)) if float(value).is_integer() and abs(value) < 1e15 else f"{value:.12g}"
    return re.sub(r"\s*\n\s*", " ", str(value)).replace("|", "\\|").strip()


def _xlsx(data: bytes) -> str:
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for sheet in book.worksheets:
            parts.append(f"## {sheet.title}")
            rows: list[list[str]] = []
            more = 0
            for row in sheet.iter_rows():
                texts = [_cell(c.value, getattr(c, "number_format", "General")) for c in row]
                if not any(texts):
                    continue
                if len(rows) < ROWS_PER_SHEET:
                    rows.append(texts)
                else:
                    more += 1
            width = max((len(r) for r in rows), default=0)
            lines = []
            for i, r in enumerate(rows):
                lines.append("| " + " | ".join((r + [""] * width)[:width]) + " |")
                if i == 0:
                    lines.append("|" + " --- |" * width)
            parts.append("\n".join(lines) if lines else "(empty)")
            if more:
                parts.append(f"... {more} more rows not shown")
    finally:
        book.close()
    return "\n\n".join(parts) + "\n"


def _pdf_pages(data: bytes) -> int:
    from pdfminer.pdfdocument import PDFDocument, PDFPasswordIncorrect
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser

    try:
        return sum(1 for _ in PDFPage.create_pages(PDFDocument(PDFParser(io.BytesIO(data)))))
    except PDFPasswordIncorrect as exc:
        raise _EncryptedError(str(exc)) from exc
    except Exception as exc:
        if type(exc).__name__ in {"PDFEncryptionError", "PDFPasswordIncorrect"}:
            raise _EncryptedError(str(exc)) from exc
        raise


class _EncryptedError(Exception):
    pass


def convert(data: bytes, kind: str) -> Converted:
    """Runs in the child: the Markdown of a file of a known kind."""
    if kind in {"csv", "text"}:
        return Converted(kind, _decode(data), None)
    if kind == "xlsx":
        try:
            return Converted(kind, _xlsx(data), None)
        except Exception as exc:
            if b"EncryptedPackage" in data[:4096]:
                raise _EncryptedError(str(exc)) from exc
            # A workbook openpyxl cannot read falls back to MarkItDown, as the knowledge converter does.
    pages = _pdf_pages(data) if kind == "pdf" else None
    from markitdown import MarkItDown, StreamInfo

    result = MarkItDown(enable_plugins=False).convert_stream(
        io.BytesIO(data), stream_info=StreamInfo(extension="." + kind)
    )
    return Converted(kind, _BLANKS.sub("\n\n", result.markdown or "").strip() + "\n", pages)


def child_main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: python -m tranibor_procurement.convert <kind> < document\n")
        return 2
    data = sys.stdin.buffer.read()
    try:
        done = convert(data, argv[1])
    except _EncryptedError as exc:
        sys.stdout.write(json.dumps({"error": ENCRYPTED, "detail": str(exc)[:300]}))
        return 1
    except Exception as exc:  # the converter's own failure, said to the parent
        sys.stdout.write(
            json.dumps({"error": CONVERTER_ERROR, "detail": f"{type(exc).__name__}: {exc}"[:300]})
        )
        return 1
    sys.stdout.write(json.dumps({"markdown": done.markdown, "pages": done.pages}))
    return 0


def _child_command(kind: str) -> list[str]:
    return [sys.executable, "-m", "tranibor_procurement.convert", kind]


def _child_env() -> dict[str, str]:
    """A bare environment: the interpreter finds this package, and nothing of the job's secrets."""
    return {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
    }


def convert_file(
    path: Path, timeout: float = DEFAULT_TIMEOUT_S, *, command: Sequence[str] | None = None
) -> Converted | Unreadable:
    """One file as Markdown, or why it cannot be read. `command` replaces the child (tests)."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return Unreadable(CONVERTER_ERROR, f"{type(exc).__name__}")
    names: list[str] = []
    if data.startswith(b"PK\x03\x04"):
        import zipfile

        with contextlib.suppress(Exception):
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    kind = kind_of(path.name, data[:16], names)
    if isinstance(kind, Unreadable):
        return kind
    try:
        done = subprocess.run(  # noqa: S603  (argv built here, never a shell)
            list(command) if command is not None else _child_command(kind),
            input=data,
            capture_output=True,
            timeout=timeout,
            env=_child_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Unreadable(TIMEOUT, f"no answer in {timeout:g} s")
    try:
        answer = json.loads(done.stdout.decode("utf-8", "replace"))
        if not isinstance(answer, dict):
            raise ValueError("not an object")
    except ValueError:
        return Unreadable(CONVERTER_ERROR, f"exit {done.returncode}")
    if "error" in answer:
        return Unreadable(str(answer["error"]), str(answer.get("detail", "")))
    if done.returncode != 0 or not isinstance(answer.get("markdown"), str):
        return Unreadable(CONVERTER_ERROR, f"exit {done.returncode}")
    pages = answer.get("pages")
    markdown = str(answer["markdown"])
    solid = sum(1 for c in markdown if not c.isspace())
    if kind == "pdf" and isinstance(pages, int) and solid < MIN_CHARS_PER_PAGE * max(pages, 1):
        return Unreadable(NO_TEXT_LAYER, f"{solid} characters on {pages} page(s)")
    if not solid:
        return Unreadable(EMPTY, "no text")
    return Converted(kind, markdown, pages if isinstance(pages, int) else None)


if __name__ == "__main__":
    sys.exit(child_main(sys.argv))

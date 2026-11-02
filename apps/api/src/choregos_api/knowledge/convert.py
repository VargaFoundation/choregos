# SPDX-License-Identifier: Apache-2.0
"""Turns one document into markdown, in a child process: `python -m choregos_api.knowledge.convert .ext`.

The bytes come on stdin, a JSON object `{"markdown", "title"}` goes to stdout. The parent kills the
process past its timeout, and the process inherits none of the API's secrets (`conversion.py`
gives it a bare environment): a hostile document reaches a parser, never a database URL or a key.

MarkItDown (MIT) keeps what matters to an agent reading the corpus: headings of a Word document,
one `#` title per slide. A workbook is read directly (`xlsx.py`, S42-09c): one table per sheet, cached
values, a row cap. A PDF yields its text; its headings are not
recovered (ADR 0051). Structured text (YAML, JSON, TOML; S51-05) is not parsed at all: it is kept
verbatim in a fenced code block, which the parent heads with the file's path.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys

_BLANKS = re.compile(r"\n{3,}")
_BACKTICKS = re.compile(r"`{3,}")


def fenced(text: str, language: str) -> str:
    """`text` as one fenced code block, the fence longer than any run of backticks inside it."""
    body = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    fence = "`" * max([3, *(len(run) + 1 for run in _BACKTICKS.findall(body))])
    return f"{fence}{language}\n{body}\n{fence}\n"


def convert(
    data: bytes, extension: str, rows_per_sheet: int | None = None
) -> tuple[str, str | None, list[str]]:
    """The markdown of a document, the title its format declares, and what it left out (S42-09e)."""
    from .formats import FENCE_LANGUAGE

    if extension in FENCE_LANGUAGE:
        return fenced(data.decode("utf-8", "replace"), FENCE_LANGUAGE[extension]), None, []
    if extension == ".xlsx":
        from .xlsx import DEFAULT_ROWS_PER_SHEET, xlsx_to_markdown_with_warnings

        # A workbook openpyxl cannot read falls back to MarkItDown.
        with contextlib.suppress(Exception):
            markdown, warnings = xlsx_to_markdown_with_warnings(
                data, rows_per_sheet or DEFAULT_ROWS_PER_SHEET
            )
            return markdown, None, warnings
    from markitdown import MarkItDown, StreamInfo

    converter = MarkItDown(enable_plugins=False)
    result = converter.convert_stream(io.BytesIO(data), stream_info=StreamInfo(extension=extension))
    markdown = _BLANKS.sub("\n\n", result.markdown or "").strip() + "\n"
    return markdown, (result.title or None), []


def to_markdown(data: bytes, extension: str, rows_per_sheet: int | None = None) -> tuple[str, str | None]:
    """The markdown of a document and the title its format declares, if any."""
    markdown, title, _ = convert(data, extension, rows_per_sheet)
    return markdown, title


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3} or (len(argv) == 3 and not argv[2].isdigit()):
        sys.stderr.write("usage: python -m choregos_api.knowledge.convert .ext [rows_per_sheet] < document\n")
        return 2
    data = sys.stdin.buffer.read()
    try:
        markdown, title, warnings = convert(data, argv[1], int(argv[2]) if len(argv) == 3 else None)
    except Exception as exc:  # the converter's own failure, said to the parent
        sys.stderr.write(f"{type(exc).__name__}: {exc}"[:2000])
        return 1
    sys.stdout.write(json.dumps({"markdown": markdown, "title": title, "warnings": warnings}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

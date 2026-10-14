# SPDX-License-Identifier: Apache-2.0
"""Turns one document into markdown, in a child process: `python -m choregos_api.knowledge.convert .ext`.

The bytes come on stdin, a JSON object `{"markdown", "title"}` goes to stdout. The parent kills the
process past its timeout, and the process inherits none of the API's secrets (`conversion.py`
gives it a bare environment): a hostile document reaches a parser, never a database URL or a key.

MarkItDown (MIT) keeps what matters to an agent reading the corpus: headings of a Word document,
one `#` title per slide, one table per sheet. A PDF yields its text; its headings are not
recovered (ADR 0051).
"""

from __future__ import annotations

import io
import json
import re
import sys

_BLANKS = re.compile(r"\n{3,}")


def to_markdown(data: bytes, extension: str) -> tuple[str, str | None]:
    """The markdown of a document and the title its format declares, if any."""
    from markitdown import MarkItDown, StreamInfo

    converter = MarkItDown(enable_plugins=False)
    result = converter.convert_stream(io.BytesIO(data), stream_info=StreamInfo(extension=extension))
    markdown = _BLANKS.sub("\n\n", result.markdown or "").strip() + "\n"
    return markdown, (result.title or None)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: python -m choregos_api.knowledge.convert .ext < document\n")
        return 2
    data = sys.stdin.buffer.read()
    try:
        markdown, title = to_markdown(data, argv[1])
    except Exception as exc:  # the converter's own failure, said to the parent
        sys.stderr.write(f"{type(exc).__name__}: {exc}"[:2000])
        return 1
    sys.stdout.write(json.dumps({"markdown": markdown, "title": title}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""The native binary's entry point (S62-07): the `choregos` console script, writing UTF-8.

A Python started by hand on Windows writes to a pipe or a file in the locale's code page (cp1252),
where `✓` and `✗` cannot be encoded: `choregos workflow validate x.yaml > out.txt` would die on its
first line. A Python has `PYTHONUTF8=1` for that; a binary without a Python gets it here.
"""

import sys

for stream in (sys.stdout, sys.stderr):
    encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    if stream is not None and encoding != "utf8" and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

from choregos_cli.main import main  # noqa: E402 - after the streams are set

main()

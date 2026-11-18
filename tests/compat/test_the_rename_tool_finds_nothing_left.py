"""ADR 0176 R2: no tracked file spells a package or a distribution by its former name.

`tools/rename_python_packages.py --check` lists the files it would still change. A story that
imports `choregos_api` (or merges a branch that does) turns this red, and the fix is to run the tool.
The shims, the history (`docs/adr`, `docs/plan`) and the recorded runs are excluded by the tool itself.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_nothing_is_left_to_rename() -> None:
    done = subprocess.run(
        [sys.executable, "tools/rename_python_packages.py", "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert done.returncode == 0, "run `uv run python tools/rename_python_packages.py`:\n" + done.stdout

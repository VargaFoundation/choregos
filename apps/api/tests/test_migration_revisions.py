# SPDX-License-Identifier: Apache-2.0
"""The core's migrations form one chain: each revision id is written once, and there is one head.

On 2026-11-12 two branches written the same day gave their migrations the same id (`f9b1d3e5a7c0`):
Alembic keeps one of the two files in its map and drops the other without failing, so a single-head
check alone stays green while a table is never created. The id is therefore read from the files
themselves, where a duplicate cannot hide (S66-22).
"""

from __future__ import annotations

import pathlib
import re
from collections import Counter

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "src" / "choregos_api" / "migrations" / "versions"
#: `revision = "x"` or `revision: str = "x"`, as the files write it.
REVISION = re.compile(r"""^revision(?:\s*:\s*[\w\[\]| ]+)?\s*=\s*["']([^"']+)["']""", re.M)


def _files() -> dict[pathlib.Path, str]:
    found: dict[pathlib.Path, str] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        revision = REVISION.search(path.read_text(encoding="utf-8"))
        if revision is not None:
            found[path] = revision.group(1)
    return found


def test_each_revision_id_is_written_once() -> None:
    files = _files()
    assert len(files) > 100, "the migrations were not read: their header changed shape"
    assert len(files) == len(list(VERSIONS.glob("*.py"))), "a migration's revision id was not read"
    counts = Counter(files.values())
    twice = {r: sorted(p.name for p, rev in files.items() if rev == r) for r, n in counts.items() if n > 1}
    assert not twice, f"revision ids written in more than one migration: {twice}"


def test_the_core_has_one_head() -> None:
    from alembic.script import ScriptDirectory
    from choregos_api.migrate import configuration

    script = ScriptDirectory.from_config(configuration(with_extensions=False))
    assert len(script.get_heads()) == 1, f"several heads: {script.get_heads()}"

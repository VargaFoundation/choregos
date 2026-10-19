"""Does `docs/development.md` say what `make api` really starts? (#441)

The page told developers to run `make api` next to `make worker` and expect the two to work
together. `make api` forces `CHOREGOS_FAKES=1`, which gives the API an in-memory Temporal that
records workflows and runs none, so nothing the API started ever reached a worker.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _section(text: str, title: str) -> str:
    start = text.index(f"## {title}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else None]


def _make_target(name: str) -> str:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(rf"^{name}:.*\n((?:\t.*\n)+)", makefile, re.M)
    assert match, f"no `{name}` target in the Makefile"
    return match.group(1)


def test_the_page_says_make_api_runs_on_fakes_and_runs_no_workflow() -> None:
    assert "CHOREGOS_FAKES=1" in _make_target("api"), "make api changed: rewrite *Local services*"
    section = _section((ROOT / "docs/development.md").read_text(encoding="utf-8"), "Local services")
    assert "CHOREGOS_FAKES=1" in section and "FakeTemporal" in section
    assert "make worker" not in section, "the API on fakes and real workers never meet"
    assert "docker compose up" in section, "point to the setup that really runs workflows"

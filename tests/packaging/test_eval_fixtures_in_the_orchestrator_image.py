"""Does the orchestrator image carry the eval matrix's reference tickets? (S12-01, S37-13)

`activities/evals.py` reads the tickets and toy repositories from `tests/fixtures`, next to the
workspace root. The 0.18.1 image left them out: a live matrix on dev found no ticket and published
four cells of zero cases. Building the image is the CI's job; this test keeps the copy, and keeps
it where the activity looks.
"""

from __future__ import annotations

import pathlib
import re

from choregos_orchestrator.activities import evals

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker/api.Dockerfile"


def _builder() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    start = re.search(r"^FROM\s+\S+\s+AS\s+builder\s*$", text, re.M | re.I)
    assert start, "the api image has a `builder` stage"
    end = re.search(r"^FROM\s", text[start.end() :], re.M)
    return text[start.end() : start.end() + end.start()] if end else text[start.end() :]


def test_the_activity_reads_the_fixtures_relative_to_the_workspace_root() -> None:
    # The image mirrors the repository under `WORKDIR /app`: the same relative path works in both.
    assert evals.TICKETS.relative_to(ROOT).as_posix() == "tests/fixtures/tickets"
    assert evals.REPOS.relative_to(ROOT).as_posix() == "tests/fixtures/repos"


def test_the_builder_copies_the_tickets_and_toy_repositories_under_app() -> None:
    builder = _builder()
    assert re.search(r"^WORKDIR\s+/app\s*$", builder, re.M)
    for fixture in ("tests/fixtures/tickets", "tests/fixtures/repos"):
        assert re.search(rf"^COPY\s+{fixture}\s+{fixture}\s*$", builder, re.M), (
            f"{fixture} must be copied into the image (S37-13)"
        )

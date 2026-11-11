"""The API image carries the user documentation of its own version (S62-10, ADR 0141).

The MCP door's `docs_search` and `docs_read` read `/opt/choregos/docs`; the image fills it from
`docs/site/**`, Markdown only. A Dockerfile that forgets the copy builds, starts, and answers "no
bundled documentation" on every instance: only this static check notices.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker" / "api.Dockerfile"


def _stages(text: str) -> dict[str, str]:
    """Each stage's body, by name (`FROM … AS name`)."""
    parts = re.split(r"^FROM\s+\S+(?:\s+AS\s+(\S+))?\s*$", text, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2) if parts[i]}


def test_the_docs_stage_keeps_the_markdown_of_docs_site() -> None:
    stage = _stages(DOCKERFILE.read_text(encoding="utf-8"))["docs"]
    assert re.search(r"^COPY\s+docs\s+/src/docs\s*$", stage, re.M), "the docs folder enters the stage"
    assert "if [ -d /src/docs/site ]" in stage, "a build without docs/site does not fail"
    assert "cd /src/docs/site" in stage
    assert "-name '*.md'" in stage and "-name '*.mdx'" in stage, "Markdown only"
    assert "cp --parents" in stage, "the tree is kept: the slugs come from the paths"


def test_the_api_target_copies_it_where_the_setting_points() -> None:
    from choregos_api.config import Settings

    stages = _stages(DOCKERFILE.read_text(encoding="utf-8"))
    copy = re.compile(r"^COPY\s+--from=docs\s+/out\s+(\S+)\s*$", re.M)
    (target,) = copy.findall(stages["api"])
    assert target == Settings.model_fields["docs_dir"].default == "/opt/choregos/docs"
    assert not copy.search(stages["worker"]), "the workers serve no MCP door"

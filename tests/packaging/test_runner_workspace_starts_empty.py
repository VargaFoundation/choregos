"""Does a run's tree start empty in the runner image? (S65-04a)

`/workspace` is the build's `$HOME`, and every build step left its caches there. On Docker a run's tree
starts as that directory (`local_docker` runs the runner on it, and a new volume is filled from it), so
the runner took a fresh tree for a replay's leftovers and checked nothing out (S65-04c keeps the files of
a tree that has content): the agent found an empty repository, and a root-owned `.headroom/` blocked the
step on scope. Measured with claude-agent-acp on the 0.27.15 image. The build empties the directory and
fails if anything is left; this keeps a later step from being added after it.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker/runner.Dockerfile"
EMPTIED = 'RUN find /workspace -mindepth 1 -delete && test -z "$(ls -A /workspace)"'


def _runtime() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    return text[text.index("AS runtime") :]


def test_the_build_empties_the_workspace_after_its_last_step() -> None:
    runtime = _runtime()
    assert EMPTIED in runtime, "the image leaves the build's caches in a run's tree"
    after = runtime.split(EMPTIED, 1)[1]
    assert not re.search(r"^(RUN|COPY|ADD) ", after, re.M), "a build step runs after the workspace is emptied"


def test_home_is_still_the_workspace_so_the_emptying_is_needed() -> None:
    """If `$HOME` moves out of `/workspace`, this test and the step can be revisited together."""
    assert re.search(r"^\s*HOME=/workspace", _runtime(), re.M)

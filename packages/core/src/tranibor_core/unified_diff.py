# SPDX-License-Identifier: Apache-2.0
"""A comparison from the SCM as one unified diff, bounded, with no secret in it (ADR 0129 §7, §8).

The SCM hands one patch per file, without its header. The whole text is what the console reads and
what a download gives; `git apply` accepts it when nothing was cut or withheld.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .domain import DiffFile, DiffSummary
from .gates import scan_secrets

NO_PATCH = "# no textual patch from the SCM for this file (empty, binary, or too large)"
CUT = "# patch cut: the diff of this branch is larger than the platform keeps"
WITHHELD = "# patch withheld: a secret was detected in this file"
PATH_WITHHELD = "(path withheld)"


#: A diff is kept for good, so it is scanned wider than the `no_secrets` guarantee (ADR 0129 §8).
_DIFF_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GitHub token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("GitLab token", re.compile(r"glpat-[A-Za-z0-9_-]{20,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # A literal only: `token = os.environ["TOKEN"]` and `password: ${{ secrets.X }}` are not secrets.
    (
        "secret assignment",
        re.compile(
            r"(?im)^\+.*?(?:token|secret|password|passwd|pwd|api[_-]?key|access[_-]?key|private[_-]?key)"
            r"[\"']?\s*[=:]\s*[\"']([^\"'\s$<{]{8,})[\"']"
        ),
    ),
)


def _secret_shapes(text: str) -> list[str]:
    return [*scan_secrets(text), *(label for label, shape in _DIFF_SHAPES if shape.search(text))]


@dataclass(frozen=True, slots=True)
class UnifiedDiff:
    """The text of a branch's diff, and what a summary of it says."""

    text: str
    files: int
    additions: int
    deletions: int
    truncated: bool = False
    withheld: list[str] = field(default_factory=list)


def _header(file: DiffFile, hidden: bool) -> list[str]:
    old = file.old_path or file.path
    if hidden:
        # The header is kept, so a path that holds the secret must not be.
        return [f"diff --git a/{PATH_WITHHELD} b/{PATH_WITHHELD}"]
    lines = [f"diff --git a/{old} b/{file.path}"]
    if file.status == "renamed":
        lines += [f"rename from {old}", f"rename to {file.path}"]
    lines.append("--- /dev/null" if file.status == "added" else f"--- a/{old}")
    lines.append("+++ /dev/null" if file.status == "removed" else f"+++ b/{file.path}")
    return lines


def _hunks(patch: str) -> str:
    """The patch from its first hunk: GitLab may repeat the `---`/`+++` header the file's own replaces."""
    start = patch.find("@@")
    return (patch if start <= 0 else patch[start:]).rstrip("\n")


def unified_diff(summary: DiffSummary, max_bytes: int) -> UnifiedDiff | None:
    """The branch's diff; `None` when the branch changes nothing.

    A secret shape in a file keeps that file's name and counters only, the others being what the scan
    found clean: a revision is never modified and is recorded before the gates run. Past `max_bytes`,
    the files that no longer fit keep their header and say they were cut.
    """
    if not summary.files:
        return None
    found: set[str] = set()
    budget = max_bytes
    truncated = False
    blocks: list[str] = []
    for file in summary.files:
        paths = (file.path, file.old_path)
        path_shapes = [label for text in paths if text for label in _secret_shapes(text)]
        shapes = {*path_shapes, *(_secret_shapes(file.patch) if file.patch else [])}
        found |= shapes
        lines = _header(file, hidden=bool(path_shapes))
        counts = f"# counts: +{file.additions} -{file.deletions}"
        textual = file.patch is not None and "@@" in file.patch
        if shapes:
            lines += [WITHHELD, counts]
        elif not textual:
            # A rename and nothing else has no patch to miss.
            if not (file.status == "renamed" and not file.additions and not file.deletions):
                lines += [NO_PATCH, counts]
        else:
            hunks = _hunks(str(file.patch))
            size = len(hunks.encode("utf-8"))
            if size > budget:
                truncated = True
                lines += [CUT, counts]
            else:
                budget -= size
                lines.append(hunks)
        blocks.append("\n".join(lines))
    return UnifiedDiff(
        text="\n".join(blocks) + "\n",
        files=len(summary.files),
        additions=summary.additions,
        deletions=summary.deletions,
        truncated=truncated,
        withheld=sorted(found),
    )

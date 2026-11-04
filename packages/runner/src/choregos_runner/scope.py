# SPDX-License-Identifier: Apache-2.0
"""Scope check: what the agent really modified.

The refused permission is a shortcut; the guarantee is here: we look at the diff,
we revert what goes beyond, we ask again once, and if some remains we block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from choregos_core import matches_any

from .workspace import RUNNER_FILES, Workspace


@dataclass
class ScopeCheck:
    """Result of a scope check."""

    changed: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    reverted: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.remaining

    def prompt(self) -> str:
        return "\n".join(
            [
                "Files outside the allowed paths were changed; I reverted them:",
                *[f"- `{path}`" for path in self.reverted],
                "",
                "If one of them is essential, call `request_scope_change(paths, justification)`.",
                "If it is a problem you noticed on the way, call `report_finding(...)`.",
                "Then resume your work within the allowed paths, and update `.choregos/result.json`.",
            ]
        )


async def check_scope(
    workspace: Workspace, base: str, allowed_paths: list[str], *, revert: bool = True
) -> ScopeCheck:
    """Compare the diff to the authorised scope and revert the overflows."""
    changed = await workspace.changed_files(base)
    ignored = set(RUNNER_FILES)
    candidates = [path for path in changed if path not in ignored]
    if not allowed_paths:
        return ScopeCheck(changed=candidates)
    out_of_scope = [path for path in candidates if not matches_any(path, allowed_paths)]
    check = ScopeCheck(changed=candidates, out_of_scope=out_of_scope)
    if out_of_scope and revert:
        check.reverted = await workspace.revert_paths(out_of_scope, base)
        still = await workspace.changed_files(base)
        check.remaining = [
            path for path in still if path not in ignored and not matches_any(path, allowed_paths)
        ]
    elif out_of_scope:
        check.remaining = list(out_of_scope)
    return check


def config_files_touched(changed: list[str], workspace_path: Path) -> list[str]:
    """Choregos configuration files modified: always suspect, never silent."""
    return [path for path in changed if path.startswith(".choregos/") and not path.endswith("result.json")]

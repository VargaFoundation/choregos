"""Vérification de périmètre : ce que l'agent a réellement modifié.

La permission refusée est un raccourci ; la garantie est ici : on regarde le diff,
on annule ce qui dépasse, on redemande une fois, et s'il en reste on bloque.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from choregos_core import matches_any

from .workspace import RUNNER_FILES, Workspace


@dataclass
class ScopeCheck:
    """Résultat d'une vérification de périmètre."""

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
                "Des fichiers hors du périmètre autorisé ont été modifiés ; je les ai annulés :",
                *[f"- `{path}`" for path in self.reverted],
                "",
                "Si l'un d'eux est indispensable, appelle `request_scope_change(paths, justification)`.",
                "Si c'est un problème que tu as remarqué au passage, appelle `report_finding(...)`.",
                "Reprends ensuite ton travail dans le périmètre, puis mets à jour `.choregos/result.json`.",
            ]
        )


async def check_scope(
    workspace: Workspace, base: str, allowed_paths: list[str], *, revert: bool = True
) -> ScopeCheck:
    """Compare le diff au périmètre autorisé et annule les débordements."""
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
    """Fichiers de configuration Choregos modifiés : toujours suspect, jamais silencieux."""
    return [path for path in changed if path.startswith(".choregos/") and not path.endswith("result.json")]

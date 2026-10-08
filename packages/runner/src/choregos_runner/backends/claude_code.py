# SPDX-License-Identifier: Apache-2.0
"""Backend Claude Code (adaptateur ACP `claude-agent-acp`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from choregos_contracts import ModelRef, StageInput

from .base import Backend, LaunchPlan


class ClaudeCodeBackend(Backend):
    name: ClassVar[str] = "claude-code"
    skills_dir: ClassVar[str | None] = ".claude/skills"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp", "mcp", "agents_md", "structured_output"})
    model_constraint: ClassVar[tuple[str, ...]] = ("claude", "anthropic")

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        # `@zed-industries/claude-code-acp` installe un binaire `claude-code-acp` — et rien
        # d'autre. Le défaut nommait `claude-agent-acp`, qui est la clé de `versions.lock`,
        # pas un exécutable : sans `agent.launch.command` explicite, le lancement échouait
        # sur « command not found ».
        command = list(stage_input.agent.launch.command) or ["claude-code-acp"]
        settings = {
            "permissions": {
                "deny": [f"Bash({command})" for command in stage_input.permissions.deny_commands],
            },
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write|Edit|MultiEdit",
                        "hooks": [
                            {
                                "type": "command",
                                "command": ".choregos/hooks/check_scope.sh",
                            }
                        ],
                    }
                ]
            },
        }
        files = {
            ".mcp.json": self.mcp_config_json(stage_input),
            ".claude/settings.json": json.dumps(settings, indent=2, ensure_ascii=False),
            ".choregos/hooks/check_scope.sh": _scope_hook(),
            "CLAUDE.md": _claude_md(stage_input),
        }
        files.update(stage_input.agent.launch.files)
        return LaunchPlan(
            command=command,
            env={"CLAUDE_PROJECT_DIR": str(workspace), **stage_input.agent.launch.env},
            files=files,
            mcp_servers=self.mcp_servers(stage_input),
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        self.validate_model(model)
        return {
            **self.anthropic_env(model, key),
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "16000",
            "DISABLE_TELEMETRY": "1",
        }


def _scope_hook() -> str:
    """Filet de sécurité local : le vrai contrôle reste la vérification du diff.

    Le script est déposé dans l'espace de travail de l'agent, qui peut le lire : ses commentaires
    sont en anglais (ADR 0039). L'histoire de l'exception `.choregos/**` est ici : elle appartient
    à la PLATEFORME, pas au périmètre du ticket — c'est là que l'agent doit écrire son
    `result.json`, exigé par le contrat de sortie. Le refuser mettait l'agent devant une
    contradiction ; il l'a écrit noir sur blanc dans sa transcription — « il y a une contradiction
    entre le périmètre autorisé et l'obligation d'écrire result.json » —, puis il abandonnait, et
    l'étape échouait sur un résultat absent.
    """
    return """#!/usr/bin/env bash
# Fallback hook: refuses a write outside the allowed paths.
# It is not the guarantee (the runner checks the diff); it is a useful shortcut.
set -euo pipefail
payload=$(cat)
path=$(printf '%s' "$payload" | grep -o '"file_path"[^,]*' | head -1 | cut -d'"' -f4 || true)
[ -z "${path:-}" ] && exit 0
rel_precoce=${path#"$PWD/"}
# `.choregos/**` belongs to the platform, not to the work item's scope: the agent must
# write its `result.json` there, as the output contract requires.
case "$rel_precoce" in
  .choregos/*) exit 0 ;;
esac
allowed_file=".choregos/allowed_paths.txt"
[ -f "$allowed_file" ] || exit 0
rel=${path#"$PWD/"}
while read -r pattern; do
  [ -z "$pattern" ] && continue
  case "$rel" in
    $pattern) exit 0 ;;
  esac
done < "$allowed_file"
echo "path outside the allowed paths: $rel (see .choregos/allowed_paths.txt)" >&2
exit 2
"""


def _claude_md(stage_input: StageInput) -> str:
    return "\n".join(
        [
            "# Instructions (Choregos)",
            "",
            "This repository follows `AGENTS.md`. In addition, for this step:",
            "",
            f"- Work item: {stage_input.work_item.key} — {stage_input.work_item.title}",
            f"- Role: {stage_input.transition.role}",
            "- Allowed paths: see `.choregos/allowed_paths.txt`.",
            "- Finish by writing `.choregos/result.json`.",
        ]
    )

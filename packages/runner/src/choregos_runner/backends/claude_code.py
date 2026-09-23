"""Backend Claude Code (adaptateur ACP `claude-agent-acp`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from choregos_contracts import ModelRef, StageInput

from .base import Backend, LaunchPlan


class ClaudeCodeBackend(Backend):
    name: ClassVar[str] = "claude-code"
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
    """Filet de sécurité local : le vrai contrôle reste la vérification du diff."""
    return """#!/usr/bin/env bash
# Hook de secours : refuse une écriture hors des chemins autorisés.
# Ce n'est pas la garantie (le runner vérifie le diff), c'est un raccourci utile.
set -euo pipefail
payload=$(cat)
path=$(printf '%s' "$payload" | grep -o '"file_path"[^,]*' | head -1 | cut -d'"' -f4 || true)
[ -z "${path:-}" ] && exit 0
rel_precoce=${path#"$PWD/"}
# `.choregos/**` appartient à la PLATEFORME, pas au périmètre du ticket : c'est là que
# l'agent doit écrire son `result.json`, exigé par le contrat de sortie. Le refuser mettait
# l'agent devant une contradiction — il l'a écrit noir sur blanc dans sa transcription :
# « il y a une contradiction entre le périmètre autorisé et l'obligation d'écrire
# result.json » — puis il abandonnait, et l'étape échouait sur un résultat absent.
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
echo "chemin hors périmètre : $rel (voir .choregos/allowed_paths.txt)" >&2
exit 2
"""


def _claude_md(stage_input: StageInput) -> str:
    return "\n".join(
        [
            "# Instructions (Choregos)",
            "",
            "Ce dépôt suit `AGENTS.md`. En plus, pour cette étape :",
            "",
            f"- Ticket : {stage_input.work_item.key} — {stage_input.work_item.title}",
            f"- Rôle : {stage_input.transition.role}",
            "- Périmètre autorisé : voir `.choregos/allowed_paths.txt`.",
            "- Termine en écrivant `.choregos/result.json`.",
        ]
    )

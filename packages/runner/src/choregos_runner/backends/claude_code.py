# SPDX-License-Identifier: Apache-2.0
"""Backend Claude Code (adaptateur ACP `claude-agent-acp`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from choregos_contracts import ModelRef, StageInput

from .base import Backend, LaunchPlan

#: The hook rtk answers as (rtk 0.50: `rtk hook claude` reads a `PreToolUse` payload).
RTK_HOOK = "rtk hook claude"
#: Where rtk reads its configuration, relative to `$HOME` (`dirs::config_dir()` on Linux).
RTK_CONFIG = ".config/rtk/config.toml"


class ClaudeCodeBackend(Backend):
    name: ClassVar[str] = "claude-code"
    skills_dir: ClassVar[str | None] = ".claude/skills"
    shell_filters: ClassVar[frozenset[str]] = frozenset({"rtk"})
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp", "mcp", "agents_md", "structured_output"})
    model_constraint: ClassVar[tuple[str, ...]] = ("claude", "anthropic")

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        # `@agentclientprotocol/claude-agent-acp` installs the `claude-agent-acp` binary (#436). The
        # image also links `claude-code-acp`, the name of the deprecated Zed package, for a stage
        # input that still names it.
        command = list(stage_input.agent.launch.command) or ["claude-agent-acp"]
        settings: dict[str, Any] = {
            # Without a `model` here the adapter selects its own default (0.16.2, `setModel()` with
            # the first model of the SDK's list): the run's model was replaced by Claude Code's
            # built-in default (see `model_env`).
            "model": stage_input.model.litellm_model,
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
        if not stage_input.permissions.builtin_web_tools:
            # ADR 0070: the CLI's own web tools are switched off by the policy, the agent or the
            # organisation. `WebSearch` runs at the model provider, outside every network layer
            # of the platform: this setting is the only lever there is.
            settings["permissions"]["deny"] += ["WebFetch", "WebSearch"]
        env = {"CLAUDE_PROJECT_DIR": str(workspace)}
        home_files: dict[str, str] = {}
        if stage_input.context.shell_filter == "rtk":
            # ADR 0055: rtk rewrites a Bash command into its filtered form before Claude Code runs
            # it. No `permissions.allow` rule is written: rtk would then answer `allow` itself
            # and skip the runner's guardrails. Its configuration goes under `$HOME`, outside
            # the repository, so it never lands in the diff.
            settings["hooks"]["PreToolUse"].append(
                {"matcher": "Bash", "hooks": [{"type": "command", "command": RTK_HOOK}]}
            )
            home_files[RTK_CONFIG] = _rtk_config(stage_input)
            env["RTK_TELEMETRY_DISABLED"] = "1"
        files = {
            ".mcp.json": self.mcp_config_json(stage_input),
            ".claude/settings.json": json.dumps(settings, indent=2, ensure_ascii=False),
            ".choregos/hooks/check_scope.sh": _scope_hook(),
            "CLAUDE.md": _claude_md(stage_input),
        }
        files.update(stage_input.agent.launch.files)
        return LaunchPlan(
            command=command,
            env={**env, **stage_input.agent.launch.env},
            files=files,
            mcp_servers=self.mcp_servers(stage_input),
            home_files=home_files,
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        self.validate_model(model)
        # Every alias Claude Code may pick resolves to the run's model. `ANTHROPIC_MODEL` alone
        # does not survive the adapter's `setModel(<alias>)`: on 2026-10-12 the gateway was asked
        # for `claude-sonnet-4-5-20250929`, and the run's key, minted for one model, was refused.
        pinned = dict.fromkeys(
            (
                "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                "ANTHROPIC_SMALL_FAST_MODEL",
                "CLAUDE_CODE_SUBAGENT_MODEL",
            ),
            model.litellm_model,
        )
        return {
            **pinned,
            **self.anthropic_env(model, key),
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "16000",
            "DISABLE_TELEMETRY": "1",
        }


def _rtk_config(stage_input: StageInput) -> str:
    """rtk's configuration for the run: the commands it must leave alone.

    A command the policy forbids is never rewritten. rtk renames some commands (`cat` becomes
    `rtk read`), and a renamed command would no longer match the policy's `deny_commands` when
    the guardrails judge it. rtk also leaves alone what `.claude/settings.json` denies, but a
    repository's own settings win over ours (`materialize` never overwrites), so the list is
    written here too. Each entry matches a command segment that starts with it.
    """
    excluded = list(
        dict.fromkeys([*stage_input.context.rtk_exclude_commands, *stage_input.permissions.deny_commands])
    )
    entries = ", ".join(json.dumps(command) for command in excluded)
    return "\n".join(
        [
            "# Written by Choregos for this run (ADR 0055).",
            "[hooks]",
            f"exclude_commands = [{entries}]",
            "",
        ]
    )


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

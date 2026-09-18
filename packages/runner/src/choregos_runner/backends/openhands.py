"""Backend OpenHands — l'agent par défaut (D2)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from choregos_contracts import ModelRef, StageInput

from .base import Backend, LaunchPlan


class OpenHandsBackend(Backend):
    name: ClassVar[str] = "openhands"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp", "mcp", "agents_md", "structured_output"})

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        command = list(stage_input.agent.launch.command) or ["openhands", "acp"]
        files = {
            ".openhands/mcp.json": self.mcp_config_json(stage_input),
            ".openhands/skills/choregos/SKILL.md": _skill_markdown(stage_input),
        }
        files.update(stage_input.agent.launch.files)
        return LaunchPlan(
            command=command,
            env={
                "OPENHANDS_WORKSPACE": str(workspace),
                "OPENHANDS_MCP_CONFIG": ".openhands/mcp.json",
                "LOG_ALL_EVENTS": "false",
                **stage_input.agent.launch.env,
            },
            files=files,
            mcp_servers=self.mcp_servers(stage_input),
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        return {
            "LLM_MODEL": model.litellm_model,
            "LLM_BASE_URL": model.base_url,
            "LLM_API_KEY": key or "",
            "LLM_TEMPERATURE": str(model.params.get("temperature", 0)),
            **self.openai_env(model, key),
        }


def _skill_markdown(stage_input: StageInput) -> str:
    return "\n".join(
        [
            "# Choregos",
            "",
            "Tu travailles pour la plateforme Choregos.",
            "",
            "- Le périmètre autorisé est dans `.choregos/allowed_paths.txt` : toute écriture ailleurs",
            "  sera refusée, puis annulée.",
            "- Signale les problèmes hors périmètre avec l'outil MCP `report_finding`.",
            "- Termine en écrivant `.choregos/result.json` (contrat `choregos/StageResult/v1`).",
            f"- Ticket : {stage_input.work_item.key} — {stage_input.work_item.title}",
        ]
    )

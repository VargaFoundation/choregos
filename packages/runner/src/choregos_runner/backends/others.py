"""Backends secondaires : Codex, Gemini CLI, Goose, OpenCode, Copilot CLI (S13-02).

Tous parlent ACP ; ce qui change est la façon de leur donner le modèle et la configuration
MCP. Chacun doit passer la suite de conformité avant d'être activé en production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from choregos_contracts import ModelRef, StageInput

from .base import Backend, LaunchPlan


class CodexBackend(Backend):
    name: ClassVar[str] = "codex"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp", "mcp", "agents_md"})

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        command = list(stage_input.agent.launch.command) or ["codex-acp"]
        config = "\n".join(
            [
                "[model]",
                f'name = "{stage_input.model.litellm_model}"',
                f'base_url = "{stage_input.model.base_url}"',
                "",
                "[sandbox]",
                'mode = "workspace-write"',
            ]
        )
        return LaunchPlan(
            command=command,
            env=dict(stage_input.agent.launch.env),
            files={".codex/config.toml": config, ".mcp.json": self.mcp_config_json(stage_input)},
            mcp_servers=self.mcp_servers(stage_input),
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        return {**self.openai_env(model, key), "CODEX_MODEL": model.litellm_model}


class GeminiCliBackend(Backend):
    name: ClassVar[str] = "gemini-cli"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp", "mcp"})

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        # `--acp` depuis gemini-cli 0.39 ; `--experimental-acp` est déprécié et affiche un
        # avertissement dans le flux stdio que l'ACP utilise.
        command = list(stage_input.agent.launch.command) or ["gemini", "--acp"]
        settings = {
            "selectedAuthType": "api-key",
            "model": stage_input.model.litellm_model,
            "mcpServers": json.loads(self.mcp_config_json(stage_input))["mcpServers"],
        }
        return LaunchPlan(
            command=command,
            env=dict(stage_input.agent.launch.env),
            files={".gemini/settings.json": json.dumps(settings, indent=2, ensure_ascii=False)},
            mcp_servers=self.mcp_servers(stage_input),
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        return {
            "GOOGLE_GEMINI_BASE_URL": model.base_url,
            "GEMINI_API_KEY": key or "",
            "GEMINI_MODEL": model.litellm_model,
            **self.openai_env(model, key),
        }


class GooseBackend(Backend):
    name: ClassVar[str] = "goose"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp", "mcp"})

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        command = list(stage_input.agent.launch.command) or ["goose", "acp"]
        config = "\n".join(
            [
                "GOOSE_PROVIDER: openai",
                f"GOOSE_MODEL: {stage_input.model.litellm_model}",
                "GOOSE_MODE: auto",
            ]
        )
        return LaunchPlan(
            command=command,
            env=dict(stage_input.agent.launch.env),
            files={".config/goose/config.yaml": config, ".mcp.json": self.mcp_config_json(stage_input)},
            mcp_servers=self.mcp_servers(stage_input),
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        return {
            **self.openai_env(model, key),
            "GOOSE_PROVIDER": "openai",
            "GOOSE_MODEL": model.litellm_model,
        }


class OpenCodeBackend(Backend):
    name: ClassVar[str] = "opencode"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp", "mcp"})

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        command = list(stage_input.agent.launch.command) or ["opencode", "acp"]
        config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "choregos": {
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {"baseURL": stage_input.model.base_url},
                    "models": {stage_input.model.litellm_model: {"name": stage_input.model.litellm_model}},
                }
            },
            "model": f"choregos/{stage_input.model.litellm_model}",
            "mcp": json.loads(self.mcp_config_json(stage_input))["mcpServers"],
        }
        return LaunchPlan(
            command=command,
            env=dict(stage_input.agent.launch.env),
            files={"opencode.json": json.dumps(config, indent=2, ensure_ascii=False)},
            mcp_servers=self.mcp_servers(stage_input),
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        return self.openai_env(model, key)


class CopilotCliBackend(Backend):
    name: ClassVar[str] = "copilot-cli"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp"})

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        command = list(stage_input.agent.launch.command) or ["copilot", "--acp"]
        return LaunchPlan(
            command=command,
            env=dict(stage_input.agent.launch.env),
            files={},
            mcp_servers=self.mcp_servers(stage_input),
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        return self.openai_env(model, key)

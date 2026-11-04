# SPDX-License-Identifier: Apache-2.0
"""Backends secondaires : Codex, Gemini CLI, Goose, OpenCode, Copilot CLI (S13-02).

All of them speak ACP; what changes is the way to give them the model and the MCP
configuration. Each must pass the conformance suite before being enabled in production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from choregos_contracts import ModelRef, StageInput

from .base import Backend, LaunchPlan


class CodexBackend(Backend):
    name: ClassVar[str] = "codex"
    skills_dir: ClassVar[str | None] = ".agents/skills"
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
            files={
                ".codex/config.toml": config,
                ".mcp.json": self.mcp_config_json(stage_input),
                **stage_input.agent.launch.files,
            },
            mcp_servers=self.mcp_servers(stage_input),
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        return {**self.openai_env(model, key), "CODEX_MODEL": model.litellm_model}


class GeminiCliBackend(Backend):
    name: ClassVar[str] = "gemini-cli"
    skills_dir: ClassVar[str | None] = ".gemini/skills"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp", "mcp"})

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        # `--acp` since gemini-cli 0.39; `--experimental-acp` is deprecated and prints a
        # warning in the stdio stream that ACP uses.
        command = list(stage_input.agent.launch.command) or ["gemini", "--acp"]
        settings = {
            "selectedAuthType": "api-key",
            "model": stage_input.model.litellm_model,
            "mcpServers": json.loads(self.mcp_config_json(stage_input))["mcpServers"],
        }
        return LaunchPlan(
            command=command,
            env=dict(stage_input.agent.launch.env),
            files={
                ".gemini/settings.json": json.dumps(settings, indent=2, ensure_ascii=False),
                **stage_input.agent.launch.files,
            },
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
    skills_dir: ClassVar[str | None] = ".goose/skills"
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
            files={
                ".config/goose/config.yaml": config,
                ".mcp.json": self.mcp_config_json(stage_input),
                **stage_input.agent.launch.files,
            },
            mcp_servers=self.mcp_servers(stage_input),
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        return {
            **self.openai_env(model, key),
            "GOOSE_PROVIDER": "openai",
            "GOOSE_MODEL": model.litellm_model,
        }


# opencode fetches its model catalogue (models.opencode.ai) at start, checks for updates and may
# download language servers. The agent uses only the `choregos` provider of its `opencode.json`, so
# none of it is needed; measured on the dev tenant (2026-10-23), a run made 8 calls to
# models.opencode.ai:443, each refused by the per-run egress proxy (ADR 0070) and listed under
# Admin > Network activity. Set in the launch plan, so it holds with or without a gateway key.
#
# S40-25b: the same runs also asked `registry.npmjs.org` for `@opencode-ai/plugin`. Measured on the
# pinned 1.18.31 with a logging registry and no network: for every config directory (the global
# one, and `<workspace>/.opencode` once it exists) opencode starts a background install of that
# package unless the directory is read-only or already holds `node_modules` with the package
# recorded in its lock. (`@ai-sdk/openai-compatible`, the provider's `npm` value, is bundled in
# the binary: no request, no `packages/` cache, nothing to pre-install.) Two things follow:
#   - `OPENCODE_CONFIG_HOME` is an XDG config root the runner image bakes with that package
#     installed (`docker/runner.Dockerfile`), because `$HOME` is the mounted workspace volume;
#   - the workspace gets a stub `.opencode` that already looks installed (`OPENCODE_STUB_FILES`).
# `OPENCODE_DISABLE_DEFAULT_PLUGINS` skips opencode's built-in npm plugins (copilot/codex auth);
# on 1.18.31 they asked for nothing, so the flag is belt and braces for a bump.
OPENCODE_CONFIG_HOME = "/opt/choregos/xdg-config"

OPENCODE_OFFLINE_ENV: dict[str, str] = {
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "XDG_CONFIG_HOME": OPENCODE_CONFIG_HOME,
}

# opencode's own check (`Npm.install`): `node_modules` exists, and every dependency named in
# `package.json` plus the one it wants (`@opencode-ai/plugin`) is a root dependency of
# `package-lock.json`. Nothing is imported from it, so the directory only has to exist.
OPENCODE_STUB_FILES: dict[str, str] = {
    ".opencode/package.json": "{}\n",
    ".opencode/package-lock.json": json.dumps(
        {"lockfileVersion": 3, "packages": {"": {"dependencies": {"@opencode-ai/plugin": "*"}}}}
    )
    + "\n",
    ".opencode/node_modules/.keep": "",
}


class OpenCodeBackend(Backend):
    name: ClassVar[str] = "opencode"
    skills_dir: ClassVar[str | None] = ".opencode/skills"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp", "mcp"})

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        command = list(stage_input.agent.launch.command) or ["opencode", "acp"]
        config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "choregos": {
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {
                        "baseURL": stage_input.model.base_url,
                        # The NAME of the variable, never the key: this file is written in
                        # the agent's workspace, so potentially read in a diff.
                        # `{env:…}` is the form opencode resolves at run time.
                        #
                        # Without this line, the `choregos` provider has NO key and the
                        # gateway answers "Authentication Error, No api key passed in":
                        # the environment's `OPENAI_API_KEY` only serves the built-in `openai`
                        # provider, not a provider declared by the configuration. Measured on
                        # 2026-09-27 in the dev tenant: `opencode run` fails without it and
                        # returns `pong` with it, on the same model and the same gateway.
                        "apiKey": "{env:OPENAI_API_KEY}",
                    },
                    "models": {stage_input.model.litellm_model: {"name": stage_input.model.litellm_model}},
                }
            },
            "model": f"choregos/{stage_input.model.litellm_model}",
            "mcp": self._mcp_for_opencode(stage_input),
        }
        return LaunchPlan(
            command=command,
            # The agent's own `launch.env` comes last, so it can still override these.
            env={**OPENCODE_OFFLINE_ENV, **stage_input.agent.launch.env},
            files={
                "opencode.json": json.dumps(config, indent=2, ensure_ascii=False),
                **OPENCODE_STUB_FILES,
                **stage_input.agent.launch.files,
            },
            mcp_servers=self.mcp_servers(stage_input),
        )

    @staticmethod
    def _mcp_for_opencode(stage_input: StageInput) -> dict[str, Any]:
        """The MCP servers in the format of **opencode**, which is not the common format.

        `mcp_config_json` returns the `mcpServers` form that Claude Code, Codex and Gemini read:
        `{"type": "http", "url": …}`. Opencode expects something else, and does not put up with
        it: it refuses the WHOLE configuration and stops:

            Configuration is invalid at /tmp/ws/opencode.json
            ↳ Expected { readonly "type": "local", … } | { readonly "type": "remote", … },
              got {"type":"http","url":"http://localhost:7777/mcp"} mcp.choregos
            ↳ Missing key mcp.choregos.enabled

        Found on 2026-09-27 by running `opencode acp` by hand in the dev tenant, with the
        configuration this backend writes. That is why no agent had ever run on this tenant:
        the refusal went to the error output, which the runner did not read, and the step died
        on an `initialize` `TimeoutError`.

        The deeper lesson: an agent configuration is not a common format. Reusing it from one
        backend to another was a saving, and it cost a silence.
        """
        servers: dict[str, Any] = {}
        for name, server in stage_input.tools.mcp.items():
            if server.url:
                servers[name] = {"type": "remote", "url": server.url, "enabled": True}
                if server.env:
                    servers[name]["headers"] = dict(server.env)
            elif server.command:
                servers[name] = {
                    "type": "local",
                    "command": list(server.command),
                    "enabled": True,
                }
                if server.env:
                    servers[name]["environment"] = dict(server.env)
        return servers

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        return self.openai_env(model, key)


class CopilotCliBackend(Backend):
    name: ClassVar[str] = "copilot-cli"
    skills_dir: ClassVar[str | None] = ".github/skills"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp"})

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        command = list(stage_input.agent.launch.command) or ["copilot", "--acp"]
        return LaunchPlan(
            command=command,
            env=dict(stage_input.agent.launch.env),
            files=dict(stage_input.agent.launch.files),
            mcp_servers=self.mcp_servers(stage_input),
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        return self.openai_env(model, key)

# SPDX-License-Identifier: Apache-2.0
"""ACP backends: how to start an agent, and how to give it its model.

A backend contains **no** business logic: it translates a `StageInput` into a
command line, environment variables and configuration files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from choregos_contracts import ApiFormat, ModelRef, StageInput


@dataclass(slots=True)
class LaunchPlan:
    """What the runner must do to start the agent."""

    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    #: Files for the agent's `$HOME`, outside the repository: what is written there never lands
    #: in the run's diff (ADR 0055: rtk's configuration).
    home_files: dict[str, str] = field(default_factory=dict)

    def materialize_home(self, home: Path) -> list[str]:
        """Writes `home_files` under the agent's `$HOME`. The directory belongs to the run, so
        nothing there is the project's, and a file left by a replay of the same run is replaced."""
        return LaunchPlan(command=[], files=self.home_files).materialize(home, overwrite=True)

    def materialize(self, workspace: Path, *, overwrite: bool = False) -> list[str]:
        """Write the backend's configuration without ever overwriting a file of the repository.

        A `CLAUDE.md` or a `.mcp.json` already present belongs to the project: it is
        authoritative. Returns the list of the files actually written, to exclude them from git.
        """
        written: list[str] = []
        for relative, content in self.files.items():
            target = workspace / relative
            if target.exists() and not overwrite:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            if relative.endswith(".sh"):
                target.chmod(0o755)
            written.append(relative)
        return written


class Backend:
    """Contract of an ACP backend."""

    name: ClassVar[str] = "base"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp"})
    model_constraint: ClassVar[tuple[str, ...]] = ()
    #: Where the backend reads an agent's skills (ADR 0033); `None`: it reads none, and the runner
    #: puts them under `.choregos/skills` with their index in the prompt.
    skills_dir: ClassVar[str | None] = None
    #: The shell filters its launch plan wires (ADR 0055). Another one asked of it is not applied,
    #: and the run says so.
    shell_filters: ClassVar[frozenset[str]] = frozenset()

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        raise NotImplementedError

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        raise NotImplementedError

    # ───────────────────────── shared tools ─────────────────────────

    @staticmethod
    def mcp_servers(stage_input: StageInput) -> list[dict[str, Any]]:
        """MCP servers mounted in the session: `choregos-tools` and the memory."""
        servers: list[dict[str, Any]] = []
        for name, server in stage_input.tools.mcp.items():
            if server.url:
                # `headers` is REQUIRED by the ACP schema for an HTTP server, even empty:
                # omitting it made `session/new` refuse with "Invalid params" and a
                # tree of zod errors where the missing field is hard to read.
                # An HTTP MCP server carries its authentication in headers; here, what
                # is declared in `env` stands in for it.
                servers.append(
                    {
                        "name": name,
                        "type": "http",
                        "url": server.url,
                        "headers": [{"name": k, "value": v} for k, v in server.env.items()],
                    }
                )
            elif server.command:
                servers.append(
                    {
                        "name": name,
                        "type": "stdio",
                        "command": server.command[0],
                        "args": list(server.command[1:]),
                        "env": [{"name": k, "value": v} for k, v in server.env.items()],
                    }
                )
        return servers

    @staticmethod
    def mcp_config_json(stage_input: StageInput) -> str:
        """MCP configuration in the most common format (`mcpServers`)."""
        servers: dict[str, Any] = {}
        for name, server in stage_input.tools.mcp.items():
            if server.url:
                servers[name] = {"type": "http", "url": server.url}
            else:
                servers[name] = {
                    "command": server.command[0],
                    "args": list(server.command[1:]),
                    "env": server.env,
                }
        return json.dumps({"mcpServers": servers}, indent=2, ensure_ascii=False)

    @staticmethod
    def openai_env(model: ModelRef, key: str | None) -> dict[str, str]:
        if not key:
            # No run key: the deployment has no gateway and the agent brings
            # its own credentials (subscription, provider key placed in the pod).
            # Writing an empty key here would OVERWRITE them: the backend would talk to the right model
            # without being able to authenticate, and the error would say "401", not "key erased".
            return {"OPENAI_MODEL": model.litellm_model}
        return {
            "OPENAI_BASE_URL": model.base_url,
            "OPENAI_API_KEY": key,
            "OPENAI_MODEL": model.litellm_model,
        }

    @staticmethod
    def anthropic_env(model: ModelRef, key: str | None) -> dict[str, str]:
        if not key:
            # Same reason as `openai_env`: without a run key, we do not touch the pod's
            # authentication variables.
            return {"ANTHROPIC_MODEL": model.litellm_model}
        return {
            "ANTHROPIC_BASE_URL": model.base_url,
            "ANTHROPIC_AUTH_TOKEN": key,
            "ANTHROPIC_API_KEY": key,
            "ANTHROPIC_MODEL": model.litellm_model,
        }

    def validate_model(self, model: ModelRef) -> None:
        """Hard constraint of the backend, checked on the **real** model.

        A platform alias (`platform/standard`) says nothing about the model's family: it is
        `provider_model`, filled in by the resolver from the gateway's catalogue, that carries it.
        Failing that, we fall back on the name called: a direct identifier
        (`anthropic/claude-sonnet-5`) is checked as is.
        """
        effective = (model.provider_model or model.litellm_model).lower()
        if self.model_constraint and not any(token in effective for token in self.model_constraint):
            raise ValueError(
                f"the backend `{self.name}` only accepts "
                f"{' / '.join(self.model_constraint)} models (got `{model.litellm_model}`"
                + (f" → `{model.provider_model}`" if model.provider_model else "")
                + ")"
            )

    @staticmethod
    def api_format(model: ModelRef) -> ApiFormat:
        return model.api_format

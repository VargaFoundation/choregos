"""Backends ACP : comment lancer un agent, et comment lui passer son modèle.

Un backend ne contient **aucune** logique métier : il traduit un `StageInput` en
ligne de commande, variables d'environnement et fichiers de configuration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from choregos_contracts import ApiFormat, ModelRef, StageInput


@dataclass(slots=True)
class LaunchPlan:
    """Ce que le runner doit faire pour démarrer l'agent."""

    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)

    def materialize(self, workspace: Path, *, overwrite: bool = False) -> list[str]:
        """Écrit la configuration du backend sans jamais écraser un fichier du dépôt.

        Un `CLAUDE.md` ou un `.mcp.json` déjà présents appartiennent au projet : ils font
        autorité. Rend la liste des fichiers effectivement écrits, pour les exclure de git.
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
    """Contrat d'un backend ACP."""

    name: ClassVar[str] = "base"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp"})
    model_constraint: ClassVar[tuple[str, ...]] = ()

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        raise NotImplementedError

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        raise NotImplementedError

    # ───────────────────────── outils partagés ─────────────────────────

    @staticmethod
    def mcp_servers(stage_input: StageInput) -> list[dict[str, Any]]:
        """Serveurs MCP montés dans la session : `choregos-tools` et la mémoire."""
        servers: list[dict[str, Any]] = []
        for name, server in stage_input.tools.mcp.items():
            if server.url:
                servers.append({"name": name, "type": "http", "url": server.url})
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
        """Configuration MCP au format le plus répandu (`mcpServers`)."""
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
        return {
            "OPENAI_BASE_URL": model.base_url,
            "OPENAI_API_KEY": key or "",
            "OPENAI_MODEL": model.litellm_model,
        }

    @staticmethod
    def anthropic_env(model: ModelRef, key: str | None) -> dict[str, str]:
        return {
            "ANTHROPIC_BASE_URL": model.base_url,
            "ANTHROPIC_AUTH_TOKEN": key or "",
            "ANTHROPIC_API_KEY": key or "",
            "ANTHROPIC_MODEL": model.litellm_model,
        }

    def validate_model(self, model: ModelRef) -> None:
        if self.model_constraint and not any(
            token in model.litellm_model.lower() for token in self.model_constraint
        ):
            raise ValueError(
                f"le backend `{self.name}` n'accepte que des modèles "
                f"{' / '.join(self.model_constraint)} (reçu `{model.litellm_model}`)"
            )

    @staticmethod
    def api_format(model: ModelRef) -> ApiFormat:
        return model.api_format

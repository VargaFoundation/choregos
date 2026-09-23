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
                # `headers` est EXIGÉ par le schéma ACP pour un serveur HTTP, même vide :
                # l'omettre faisait refuser `session/new` avec « Invalid params » et une
                # arborescence d'erreurs zod où le champ manquant est difficile à lire.
                # Un serveur MCP HTTP porte son authentification par en-têtes ; ici, ce qui
                # est déclaré dans `env` en tient lieu.
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
        if not key:
            # Aucune clé de run : le déploiement n'a pas de passerelle et l'agent apporte
            # ses propres identifiants (abonnement, clé fournisseur posée dans le pod).
            # Écrire ici une clé vide les ÉCRASERAIT — le backend parlerait au bon modèle
            # sans pouvoir s'authentifier, et l'erreur dirait « 401 », pas « clé effacée ».
            return {"OPENAI_MODEL": model.litellm_model}
        return {
            "OPENAI_BASE_URL": model.base_url,
            "OPENAI_API_KEY": key,
            "OPENAI_MODEL": model.litellm_model,
        }

    @staticmethod
    def anthropic_env(model: ModelRef, key: str | None) -> dict[str, str]:
        if not key:
            # Même raison que `openai_env` : sans clé de run, on ne touche pas aux variables
            # d'authentification du pod.
            return {"ANTHROPIC_MODEL": model.litellm_model}
        return {
            "ANTHROPIC_BASE_URL": model.base_url,
            "ANTHROPIC_AUTH_TOKEN": key,
            "ANTHROPIC_API_KEY": key,
            "ANTHROPIC_MODEL": model.litellm_model,
        }

    def validate_model(self, model: ModelRef) -> None:
        """Contrainte dure du backend, vérifiée sur le modèle **réel**.

        Un alias de plateforme (`platform/standard`) ne dit rien de la famille du modèle : c'est
        `provider_model`, rempli par le resolver depuis le catalogue du gateway, qui la porte. À
        défaut, on retombe sur le nom appelé — un identifiant direct (`anthropic/claude-sonnet-5`)
        se vérifie tel quel.
        """
        effective = (model.provider_model or model.litellm_model).lower()
        if self.model_constraint and not any(token in effective for token in self.model_constraint):
            raise ValueError(
                f"le backend `{self.name}` n'accepte que des modèles "
                f"{' / '.join(self.model_constraint)} (reçu `{model.litellm_model}`"
                + (f" → `{model.provider_model}`" if model.provider_model else "")
                + ")"
            )

    @staticmethod
    def api_format(model: ModelRef) -> ApiFormat:
        return model.api_format

"""Configuration d'un projet (schemas/project.schema.json)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AnyUrl, Field, field_validator

from .workflow import Slug, Strict


class ModelProfile(Strict):
    litellm_model: str
    params: dict[str, Any] = Field(default_factory=dict)
    max_turns_factor: float = Field(default=1.0, gt=0)


class RepoConfig(Strict):
    url: str
    default_branch: str = "main"
    language: Literal["python", "node", "go", "java", "dotnet", "rust", "other"] | None = None
    clone_depth: int = Field(default=50, ge=1)
    branch_prefix: str = "choregos/"
    test_command: str | None = None
    lint_command: str | None = None
    typecheck_command: str | None = None


class AgentConfig(Strict):
    default_backend: str = "claude-code"
    allowed_backends: list[str] = Field(default_factory=list)


class ModelsConfig(Strict):
    profiles: dict[str, ModelProfile] = Field(default_factory=dict)
    allow_unvalidated: bool = False

    @field_validator("profiles", mode="before")
    @classmethod
    def _accept_shorthand(cls, value: Any) -> Any:
        """`standard: anthropic/claude-sonnet-5` équivaut à `{litellm_model: …}`."""
        if not isinstance(value, dict):
            return value
        return {k: ({"litellm_model": v} if isinstance(v, str) else v) for k, v in value.items()}


class GitopsConfig(Strict):
    repo_url: str | None = None
    path_prefix: str = "apps"
    apps: list[str] = Field(default_factory=list)


class NotifyConfig(Strict):
    slack_channel: str | None = None
    emails: list[str] = Field(default_factory=list)


class ProjectConfig(Strict):
    slug: Slug
    org: Slug
    display_name: str | None = None
    template_ref: str | None = Field(default=None, pattern=r"^[a-z0-9-]+@[0-9]+\.[0-9]+\.[0-9]+$")
    #: Le dépôt du projet — **facultatif**. Un métier qui instruit des dossiers, qualifie
    #: des profils ou rédige des courriers n'en a pas, et devait jusqu'ici en déclarer un
    #: qui ne servait à rien (limite n°1 de l'ADR 0012). Sans dépôt, l'agent travaille dans
    #: un répertoire vide : rien à cloner, rien à pousser, et les garanties qui lisent un
    #: diff refusent — elles n'ont rien à lire.
    repo: RepoConfig | None = None
    envs: list[str] = Field(default_factory=lambda: ["dev", "staging", "prod"])
    agent: AgentConfig = Field(default_factory=AgentConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    gitops: GitopsConfig | None = None
    cluster: str | None = None
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    labels: dict[str, str] = Field(default_factory=dict)

    @property
    def has_repo(self) -> bool:
        """Ce projet a-t-il un dépôt ? Tout ce qui parle de git passe par là."""
        return self.repo is not None

    @property
    def repo_url(self) -> str:
        """L'URL du dépôt. Lève si le projet n'en a pas — l'appelant doit avoir vérifié.

        Rendre une chaîne vide ferait cloner « rien » et échouer trois couches plus loin,
        sur un message qui ne parlerait pas du dépôt absent.
        """
        if self.repo is None:
            raise ValueError(f"le projet {self.slug} n'a pas de dépôt")
        return self.repo.url

    def branch_for(self, key: str, slug_hint: str = "") -> str:
        """Nom de branche déterministe pour un ticket."""
        if self.repo is None:
            raise ValueError(f"le projet {self.slug} n'a pas de dépôt")
        number = key.rsplit("#", 1)[-1] if "#" in key else key.replace("/", "-")
        suffix = f"-{slug_hint}" if slug_hint else ""
        return f"{self.repo.branch_prefix}{number}{suffix}"


__all__ = [
    "AgentConfig",
    "AnyUrl",
    "GitopsConfig",
    "ModelProfile",
    "ModelsConfig",
    "NotifyConfig",
    "ProjectConfig",
    "RepoConfig",
]

# SPDX-License-Identifier: Apache-2.0
"""Configuration of a project (schemas/project.schema.json)."""

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


class DodConfig(Strict):
    """What the RUNNER measures itself, as opposed to what the agent tells.

    `repo.test_command` and its neighbours live under `repo`, so a project without a repository (a
    trade that processes case files, qualifies profiles, maintains a platform) could measure NO
    proof. `evidence_facts` then read facts the agent had written, and a narrated fact is not
    worth a measured one (ADR 0010, "the gates are mechanisms").

    `facts` maps a fact name to a command: **exit code 0 = true**, anything else = false. The
    runner executes it after the stage and the measured fact overwrites the agent's, exactly as
    the tests overwrite `tests_passed`. A command that does not exist therefore returns `false`,
    never "unknown": it is the same choice as for a guarantee without matter.

        dod:
          facts:
            constat_resolu: "ops/healthcheck/collectors/20-k3s.sh | grep -qv CLE-DU-CONSTAT"
            piece_identite: "test -f dossier/identite.pdf"
    """

    facts: dict[str, str] = Field(default_factory=dict)


class AgentConfig(Strict):
    default_backend: str = "claude-code"
    allowed_backends: list[str] = Field(default_factory=list)


class ModelsConfig(Strict):
    profiles: dict[str, ModelProfile] = Field(default_factory=dict)
    allow_unvalidated: bool = False

    @field_validator("profiles", mode="before")
    @classmethod
    def _accept_shorthand(cls, value: Any) -> Any:
        """`standard: anthropic/claude-sonnet-5` is equivalent to `{litellm_model: …}`."""
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


class ScheduleConfig(Strict):
    """A work item the platform opens by itself, on a cron (S38-11).

    The key of the work item is made of the schedule's name and the start of its window
    (`RESEARCH-weekly-20261019T0600Z`): two firings of the same window find the same key and open
    one work item, whatever retried them. The cron is UTC, five fields, minute resolution.

        schedules:
          - name: weekly
            cron: "0 6 * * mon"
            title: Weekly campaign
            fields: { symbols: [SPY.US, QQQ.US], start: "2018-01-01", end: "{window}" }
    """

    name: Slug
    cron: str
    title: str = Field(min_length=1, max_length=500)
    fields: dict[str, Any] = Field(default_factory=dict)
    workflow: str | None = None

    @field_validator("cron")
    @classmethod
    def _five_fields(cls, value: str) -> str:
        if len(value.split()) != 5:
            raise ValueError("a cron has five fields: minute hour day-of-month month day-of-week")
        return value


#: What a project's A2A skills take in file parts by default, and the most it may set (ADR 0078 §6).
A2A_MAX_FILE_BYTES_DEFAULT = 10 * 1024 * 1024
A2A_MAX_FILE_BYTES_CEILING = 50 * 1024 * 1024


class A2aConfig(Strict):
    """What the A2A door takes for this project's skills (ADR 0078 §6).

    `max_file_bytes` bounds the file parts of one message: each one, and all of them together,
    once decoded. A file part becomes an input artefact of the work item.
    """

    max_file_bytes: int = Field(default=A2A_MAX_FILE_BYTES_DEFAULT, ge=1, le=A2A_MAX_FILE_BYTES_CEILING)


class ProjectConfig(Strict):
    slug: Slug
    org: Slug
    display_name: str | None = None
    template_ref: str | None = Field(default=None, pattern=r"^[a-z0-9-]+@[0-9]+\.[0-9]+\.[0-9]+$")
    #: The project's repository: **optional**. A trade that processes case files, qualifies
    #: profiles or drafts letters has none, and until now had to declare one that served no
    #: purpose (limit no. 1 of ADR 0012). Without a repository, the agent works in an empty
    #: directory: nothing to clone, nothing to push, and the guarantees that read a diff
    #: refuse, as they have nothing to read.
    repo: RepoConfig | None = None
    envs: list[str] = Field(default_factory=lambda: ["dev", "staging", "prod"])
    agent: AgentConfig = Field(default_factory=AgentConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    gitops: GitopsConfig | None = None
    cluster: str | None = None
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    #: What the runner measures itself. At the root, not under `repo`: a project without a
    #: repository must be able to prove something.
    dod: DodConfig = Field(default_factory=DodConfig)
    #: The catalogue tools this project may call, by name. Empty = none, and that is the
    #: default: a deployed catalogue does not open to every project by accident. `["*"]`
    #: opens the whole catalogue, which is decided, not suffered.
    #:
    #: It used to be an entry of `labels`, a catch-all of tags, while we checked that the
    #: catalogue was useful. A list of permissions is not a tag.
    tools: list[str] = Field(default_factory=list)
    #: The organisation groups this project belongs to. They decide what the **deployment**
    #: opens to it: a catalogue tool marked `groups: [hr]` is only obtained by a project in
    #: the `hr` group, even if it declares it in `tools`. The project chooses what it uses;
    #: it does not choose what it is entitled to.
    groups: list[str] = Field(default_factory=list)
    #: Work items the platform opens by itself (S38-11). Needs the internal tracker: an external
    #: tracker is where its requests are created.
    schedules: list[ScheduleConfig] = Field(default_factory=list)
    #: What the A2A door takes for this project's skills (ADR 0078 §6).
    a2a: A2aConfig = Field(default_factory=A2aConfig)
    labels: dict[str, str] = Field(default_factory=dict)

    @property
    def has_repo(self) -> bool:
        """Does this project have a repository? Everything that talks to git goes through here."""
        return self.repo is not None

    @property
    def repo_url(self) -> str:
        """The repository URL. Raises if the project has none: the caller must have checked.

        Returning an empty string would clone "nothing" and fail three layers further on, on a
        message that would not mention the missing repository.
        """
        if self.repo is None:
            raise ValueError(f"the project {self.slug} has no repository")
        return self.repo.url

    def branch_for(self, key: str, slug_hint: str = "") -> str:
        """Deterministic branch name for a work item."""
        if self.repo is None:
            raise ValueError(f"the project {self.slug} has no repository")
        number = key.rsplit("#", 1)[-1] if "#" in key else key.replace("/", "-")
        suffix = f"-{slug_hint}" if slug_hint else ""
        return f"{self.repo.branch_prefix}{number}{suffix}"


__all__ = [
    "A2A_MAX_FILE_BYTES_CEILING",
    "A2A_MAX_FILE_BYTES_DEFAULT",
    "A2aConfig",
    "AgentConfig",
    "AnyUrl",
    "GitopsConfig",
    "ModelProfile",
    "ModelsConfig",
    "NotifyConfig",
    "ProjectConfig",
    "RepoConfig",
    "ScheduleConfig",
]

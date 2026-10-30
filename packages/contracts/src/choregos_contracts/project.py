# SPDX-License-Identifier: Apache-2.0
"""Configuration d'un projet (schemas/project.schema.json)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import AnyUrl, Field, field_validator, model_validator

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
    """Ce que le RUNNER mesure lui-même, par opposition à ce que l'agent raconte.

    `repo.test_command` et ses voisines vivent sous `repo`, donc un projet sans dépôt — un
    métier qui instruit des dossiers, qualifie des profils, entretient une plateforme — ne
    pouvait mesurer AUCUNE preuve. `evidence_facts` lisait alors des faits que l'agent avait
    écrits, et un fait narré ne vaut pas un fait mesuré (ADR 0010, « les gates sont des
    mécanismes »).

    `facts` associe un nom de fait à une commande : **code de sortie 0 = vrai**, autre chose =
    faux. Le runner l'exécute après l'étape et le fait mesuré écrase celui de l'agent, exactement
    comme les tests écrasent `tests_passed`. Une commande qui n'existe pas rend donc `false`,
    jamais « on ne sait pas » : c'est le même choix que pour une garantie sans matière.

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


#: A JSON pointer into a trigger's payload (RFC 6901): `/incident/number`, `/labels/0`.
JSON_POINTER = r"^(/([^~/]|~[01])*)+$"
#: A `{/pointer}` placeholder in a trigger's text: what the payload holds there, or nothing.
TRIGGER_PLACEHOLDER = r"\{(/[^{}]*)\}"


class StartRule(Strict):
    """A tracker item that starts its work item without the `agent-ready` label (S51-03, ADR 0113).

    Read when a tracker event names an item the project does not hold yet: the first rule whose
    every condition holds opens the work item and starts it, as `agent-ready` does. A rule without a
    condition is refused — it would start every item of the tracker.

        start_rules:
          - item_type: incident
            states: [New, "1"]          # the item's state in its tracker
            labels_any: [payroll]
    """

    labels_any: list[str] = Field(default_factory=list)
    labels_all: list[str] = Field(default_factory=list)
    item_type: str | None = None
    states: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _not_everything(self) -> StartRule:
        if not (self.labels_any or self.labels_all or self.item_type or self.states):
            raise ValueError("a start rule without a condition would start every item of the tracker")
        return self

    def matches(self, labels: set[str], item_type: str | None, state: str | None) -> bool:
        """Every condition holds; the type and the state compare without case, and a value the item
        does not carry never matches. The webhook and every tracker's backup poll ask this."""
        if self.labels_any and not labels & set(self.labels_any):
            return False
        if not set(self.labels_all) <= labels:
            return False
        if self.item_type and (item_type or "").lower() != self.item_type.lower():
            return False
        return not self.states or (state or "").lower() in {s.lower() for s in self.states}

    @property
    def labels_only(self) -> bool:
        """A rule a tracker that searches by labels alone (GitHub, GitLab) can ask for."""
        return not (self.item_type or self.states)

    def label_sets(self) -> list[list[str]]:
        """The label searches (each an AND of labels) whose union is this rule's items."""
        if not self.labels_any:
            return [list(self.labels_all)]
        sets = [list(dict.fromkeys([*self.labels_all, label])) for label in self.labels_any]
        return [list(found) for found in dict.fromkeys(tuple(each) for each in sets)]


class TriggerConfig(Strict):
    """An HTTP door that opens a work item when another system calls it (S51-01, ADR 0113).

    The caller posts JSON to `/webhooks/triggers/{org}/{project}/{name}` with the trigger's own
    token; the platform maps the payload onto a work item and starts it in `workflow`, or in the
    workflow the project's routing picks from its labels (`trigger:<name>` and those below). Text
    takes `{/pointer}` placeholders; a field takes a pointer and keeps the value's JSON type. Needs
    the internal tracker, as `schedules` does.

        triggers:
          - name: incident
            title: "{/number} — {/short_description}"
            key: /sys_id                 # one work item per key: a second call finds the first
            labels: ["{/category}", "p{/priority}"]
            fields: { incident: /number, ci: /cmdb_ci }
            workflow: incident-triage    # optional; the project's routing otherwise
            when: { /state: ["1", "2"] } # optional: other payloads are acknowledged and ignored
            auth: signature              # optional: an HMAC of the body instead of the bearer token

    `when` (S51-05) maps a pointer to the text, or one of the texts, it must hold; every pointer must
    match. `auth: signature` (S51-04) takes `X-Choregos-Signature: sha256=<HMAC-SHA256 of the body>`
    (hex or base64) keyed by the signing secret shown with the token, and no bearer token.
    """

    name: Slug
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=2000)
    key: str | None = Field(default=None, pattern=JSON_POINTER)
    labels: list[str] = Field(default_factory=list, max_length=20)
    fields: dict[str, str] = Field(default_factory=dict)
    workflow: str | None = None
    when: dict[str, str | list[str]] = Field(default_factory=dict)
    auth: Literal["token", "signature"] = "token"

    @field_validator("when")
    @classmethod
    def _when_pointers(cls, value: dict[str, str | list[str]]) -> dict[str, str | list[str]]:
        for pointer, expected in value.items():
            if not re.fullmatch(JSON_POINTER, pointer):
                raise ValueError(f"when: `{pointer}` is not a JSON pointer (`/a/b`)")
            if isinstance(expected, list) and not expected:
                raise ValueError(f"when: `{pointer}` lists no value, so nothing would ever match")
        return value

    @field_validator("fields")
    @classmethod
    def _pointers(cls, value: dict[str, str]) -> dict[str, str]:
        for name, pointer in value.items():
            if not re.fullmatch(JSON_POINTER, pointer):
                raise ValueError(f"field `{name}`: `{pointer}` is not a JSON pointer (`/a/b`)")
        return value

    @field_validator("title", "body")
    @classmethod
    def _placeholders(cls, value: str) -> str:
        return _checked_placeholders(value)

    @field_validator("labels")
    @classmethod
    def _label_placeholders(cls, value: list[str]) -> list[str]:
        return [_checked_placeholders(label) for label in value]


def _checked_placeholders(text: str) -> str:
    """Refuses a `{/…}` placeholder that is not a JSON pointer: it would render as nothing, silently."""
    for pointer in re.findall(TRIGGER_PLACEHOLDER, text):
        if not re.fullmatch(JSON_POINTER, pointer):
            raise ValueError(f"`{{{pointer}}}` is not a JSON pointer placeholder (`{{/a/b}}`)")
    return text


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
    #: Ce que le runner mesure lui-même. À la racine, et non sous `repo` : un projet sans dépôt
    #: doit pouvoir prouver quelque chose.
    dod: DodConfig = Field(default_factory=DodConfig)
    #: Les outils du catalogue que ce projet peut appeler, par leur nom. Vide = aucun, et
    #: c'est le défaut : un catalogue déployé ne s'ouvre pas à tous les projets par
    #: accident. `["*"]` ouvre tout le catalogue, ce qui se décide, pas se subit.
    #:
    #: C'était une entrée de `labels` — un fourre-tout d'étiquettes — le temps de vérifier
    #: que le catalogue servait. Une liste d'autorisations n'est pas une étiquette.
    tools: list[str] = Field(default_factory=list)
    #: Les groupes de l'organisation auxquels ce projet appartient. Ils décident de ce que
    #: le **déploiement** lui ouvre : un outil du catalogue marqué `groups: [rh]` n'est
    #: obtenu que par un projet du groupe `rh`, même s'il le déclare dans `tools`.
    #: Le projet choisit ce dont il se sert ; il ne choisit pas ce à quoi il a droit.
    groups: list[str] = Field(default_factory=list)
    #: Work items the platform opens by itself (S38-11). Needs the internal tracker: an external
    #: tracker is where its requests are created.
    schedules: list[ScheduleConfig] = Field(default_factory=list)
    #: HTTP doors that open a work item when another system calls them (S51-01, ADR 0113). Needs the
    #: internal tracker, for the same reason.
    triggers: list[TriggerConfig] = Field(default_factory=list)
    #: Tracker items that start without `agent-ready` (S51-03, ADR 0113): a ServiceNow incident in
    #: the desk's queue, a Jira bug of a given type. Empty: only `agent-ready` starts, as before.
    start_rules: list[StartRule] = Field(default_factory=list)
    #: What the A2A door takes for this project's skills (ADR 0078 §6).
    a2a: A2aConfig = Field(default_factory=A2aConfig)
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("triggers")
    @classmethod
    def _one_trigger_per_name(cls, value: list[TriggerConfig]) -> list[TriggerConfig]:
        names = [t.name for t in value]
        if twice := sorted({n for n in names if names.count(n) > 1}):
            raise ValueError(f"two triggers named {', '.join(f'`{n}`' for n in twice)}: a URL names one")
        return value

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
            raise ValueError(f"the project {self.slug} has no repository")
        return self.repo.url

    def branch_for(self, key: str, slug_hint: str = "") -> str:
        """Nom de branche déterministe pour un ticket."""
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
    "StartRule",
    "TriggerConfig",
]

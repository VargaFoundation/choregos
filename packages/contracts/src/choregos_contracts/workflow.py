# SPDX-License-Identifier: Apache-2.0
"""Modèles du DSL de workflow (schemas/workflow.schema.json)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import ActorType, StateKind

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]

AGENT_WILDCARD = "*agent"


class Strict(BaseModel):
    """Base : refus des champs inconnus, comme les schémas JSON."""

    model_config = ConfigDict(extra="forbid", frozen=False, use_enum_values=False)


class WorkflowCatalogueInfo(Strict):
    """How the workflow catalogue presents a workflow (ADR 0108); the engine ignores it."""

    title: str | None = Field(default=None, min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=400)
    tags: list[Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")]] = Field(default_factory=list, max_length=12)


class WorkflowMetadata(Strict):
    name: Slug
    version: int = Field(ge=1)
    description: str | None = None
    extends: str | None = Field(default=None, pattern=r"^template:[a-z0-9-]+@[0-9]+$")
    #: Le JSON Schema des CHAMPS d'un ticket de ce workflow (ADR 0031) : la date d'arrivée et le poste
    #: d'un onboarding, par exemple. Validés à la naissance du ticket, transmis aux agents.
    inputs: dict[str, Any] | None = None
    catalogue: WorkflowCatalogueInfo | None = None


#: Un rôle d'agent. Les rôles du paquet (`StageRole`) gardent leur sens — la plateforme
#: s'appuie sur `implement`, `review` et `verify` pour ses mesures et pour la revue croisée
#: — mais **un métier nomme les siens** : `sourcing`, `qualification`, `instruction_dossier`.
#:
#: Avant le 2026-09-24 il fallait écrire `role: custom` + `playbook: sourcing` : cela
#: fonctionnait, mais le board affichait « custom » pour toutes les étapes d'un métier, et
#: les évals ne savaient pas de quoi il s'agissait (ADR 0012, limite n°4). Le playbook se
#: résolvant déjà par nom de rôle, ouvrir l'énumération suffisait.
Role = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")]


class AgentActor(Strict):
    type: Literal[ActorType.AGENT] = ActorType.AGENT
    role: Role
    model: str = "profile:by_size"
    backend: str | None = None
    fresh_context: bool = False
    max_turns: int | None = Field(default=None, ge=1, le=1000)
    max_minutes: int | None = Field(default=None, ge=1, le=480)
    playbook: str | None = None
    #: Un agent du registre (ADR 0033) : `slug` ou `slug@version` ; ses instructions priment sur
    #: le playbook.
    agent: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{1,62}(@[1-9][0-9]*)?$")

    @property
    def playbook_name(self) -> str:
        return self.playbook or str(self.role)


class HumanActor(Strict):
    type: Literal[ActorType.HUMAN] = ActorType.HUMAN
    group: str
    sla_hours: int | None = Field(default=None, ge=1)
    escalate_to: str | None = None


class SystemActor(Strict):
    type: Literal[ActorType.SYSTEM] = ActorType.SYSTEM


#: The name of a job image the platform knows (`CHOREGOS_JOB_IMAGES`), never a free image reference:
#: what runs in a pod is chosen by whoever operates the platform, not by whoever writes a workflow.
JobImage = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")]


class JobTools(Strict):
    """The operations of an organisation connector a job may call through the internal API, like an
    agent's `mcp_servers`: a connector named without operations opens nothing, everything is `*`."""

    connector: Annotated[str, Field(min_length=1, max_length=64)]
    tools: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(min_length=1, max_length=32)


class JobActor(Strict):
    """A deterministic step (ADR 0056): a command, run by the runner in the step's sandbox, with no
    model, no gateway key and no agent loop.

    A backtest, a data snapshot or a conversion is code, not judgement: an agent asked to compute a
    Sharpe ratio narrates one. The job runs the same pod, the same declared inputs and outputs and the
    same gates as an agent step; what it writes in `.choregos/job.json` is *measured*, because the
    platform ran the code that produced it — whereas an agent's facts are what it says.
    """

    type: Literal[ActorType.JOB] = ActorType.JOB
    role: Role
    #: argv, never a shell line: no word splitting, no expansion, no injection from a ticket field.
    command: list[Annotated[str, Field(min_length=1, max_length=4096)]] = Field(min_length=1, max_length=64)
    #: The job image's name; `None` runs in the runner image.
    image: JobImage | None = None
    max_minutes: int | None = Field(default=None, ge=1, le=480)
    #: The connector operations the command may call with the run's token (`marketdata: [get_bars]`):
    #: the only way out of the sandbox, metered and recorded like an agent's tool calls.
    tools: list[JobTools] = Field(default_factory=list, max_length=16)


Actor = Annotated[AgentActor | HumanActor | SystemActor | JobActor, Field(discriminator="type")]
#: The actors whose step a runner executes: they share the run's lifecycle (start, await, result).
RUN_ACTORS = (AgentActor, JobActor)


class TrackerMapping(Strict):
    status: str | None = None
    label: str | None = None


class State(Strict):
    display: str = Field(min_length=1)
    tracker: TrackerMapping | None = None
    terminal: bool = False
    kind: StateKind = StateKind.WORK
    #: Un état de PRODUCTION : il ne s'atteint que par un train de release (`via: release_train`).
    #: Avant, c'était le préfixe de son nom (`deployed_prod`) : un renommage levait le verrou (#175).
    production: bool = False

    @model_validator(mode="after")
    def _coherent_kind(self) -> State:
        if self.terminal and self.kind is not StateKind.TERMINAL:
            object.__setattr__(self, "kind", StateKind.TERMINAL)
        return self


class Retry(Strict):
    to: Identifier
    max_attempts: int = Field(ge=1, le=10)
    escalate_to: Identifier


class GateSpec(Strict):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class TrainSpec(Strict):
    env: str
    approval: Identifier | None = None
    auto_sync: bool = False


class ReviewHumans(Strict):
    group: str
    required: Literal["always", "by_policy", "never"]


class ReviewSpec(Strict):
    agents: list[Identifier] = Field(default_factory=list)
    humans: ReviewHumans | None = None


class ActionEffectSpec(Strict):
    """Un effet d'une action (ADR 0035) : son nom déclaré (`connector.call`…), ses paramètres —
    rendus en Jinja isolé avec `params` et `effects` —, et ce qui le défait si la suite échoue."""

    effect: str = Field(min_length=1, max_length=128)
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    compensate: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ActionApprover(Strict):
    role: Literal["developer", "release_captain", "project_owner", "org_admin"] = "project_owner"
    #: An identity-provider group the approver must ALSO belong to (ADR 0052): a rank says how
    #: much someone may decide, a group says who is competent to — `netops-approvers` for a firewall.
    group: str | None = Field(default=None, min_length=1, max_length=200)
    min: int = Field(default=1, ge=1, le=5)


class ActionApprovalSpec(Strict):
    approvers: list[ActionApprover] = Field(default_factory=lambda: [ActionApprover()], min_length=1)
    step_up_minutes: int = Field(default=10, ge=1, le=120)
    separation_of_duties: bool = True


#: `fields.<champ>`, décalé ou non : `fields.date_arrivee - 10d`, `fields.date_depart + 2h`.
NOT_BEFORE = r"^fields\.[a-z_][a-z0-9_]{0,62}(\s*[+-]\s*[0-9]{1,4}\s*[dhm])?$"


class TransitionAction(Strict):
    """L'action gouvernée qu'une transition SYSTÈME propose (ADR 0035, S20-05).

    `title`, `justification` et `params` sont rendus en Jinja isolé avec les champs du ticket
    (`fields`) et le ticket (`work_item`) ; les effets, plus tard, avec `params`. La transition
    attend `not_before` — une date tirée d'un champ, réarmée quand le champ change —, propose
    l'action, et passe quand elle a RÉUSSI (`action_succeeded`).

    Une validation humaine est exigée quand une opération appelée l'exige pour ce projet, ou
    quand `approval` est déclaré : le workflow peut en AJOUTER une, jamais en retirer.
    """

    kind: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    title: str = Field(min_length=1, max_length=300)
    justification: str | None = Field(default=None, max_length=4000)
    params: dict[str, Any] = Field(default_factory=dict)
    effects: list[ActionEffectSpec] = Field(min_length=1, max_length=50)
    approval: ActionApprovalSpec | None = None
    not_before: str | None = Field(default=None, pattern=NOT_BEFORE)


class TaskSpec(Strict):
    """Une TÂCHE qu'une personne fait pour que le ticket passe (S20-06) : remettre un badge, déballer
    un poste. Elle remplit un formulaire — un JSON Schema d'objet dont les propriétés sont des champs
    du ticket : les valeurs y sont versées, et l'action suivante les lit — et atteste ce qu'elle a
    fait, mot pour mot. La décision garde l'une et l'autre : c'est la preuve."""

    title: str | None = Field(default=None, max_length=200)
    instructions: str | None = Field(default=None, max_length=4000)
    form: dict[str, Any]
    attest: str | None = Field(default=None, max_length=500)


#: The list a fan-out walks: a field of the work item, or one of its outputs, then keys into it.
FOR_EACH = r"^(fields|outputs)(\.[A-Za-z0-9_-]{1,64}){1,8}$"


class SpawnSpec(Strict):
    """A fan-out and its join (S38-12, ADR 0057): one CHILD work item per element of a list the
    parent holds, in the workflow `workflow` names; the transition passes once every child is
    settled — closed, in a terminal state or stopped.

    `title` and `fields` are rendered in sandboxed Jinja with `item` (the element), `rank` (from
    1), the parent's `fields` and `outputs`, and `parent` (`key`, `title`); a string that is a
    single `{{ … }}` keeps its type. The join writes `documents.children` on the parent. A child
    ending in a state listed in `succeeded` counts as a success; fewer than `min_succeeded` fails
    the transition, which then follows `on_fail`."""

    workflow: Slug
    for_each: str = Field(pattern=FOR_EACH)
    title: str = Field(min_length=1, max_length=300)
    fields: dict[str, Any] = Field(default_factory=dict)
    max_children: int = Field(default=20, ge=1, le=100)
    succeeded: list[Identifier] = Field(default_factory=list, max_length=20)
    min_succeeded: int = Field(default=0, ge=0, le=100)


class Transition(Strict):
    id: Identifier | None = None
    from_: str = Field(alias="from")
    to: Identifier
    by: Identifier | None = None
    via: Literal["release_train"] | None = None
    train: TrainSpec | None = None
    gates: list[GateSpec] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    #: The type of each declared output (ADR 0049): `deck: pptx` is a file the agent writes in
    #: `.choregos/outputs/`; an output not listed is an inline value (markdown or JSON), as before.
    output_types: dict[str, str] = Field(default_factory=dict)
    on_fail: Retry | None = None
    on_reject: Identifier | None = None
    on_changes_requested: Retry | None = None
    review: ReviewSpec | None = None
    timeout_hours: int | None = Field(default=None, ge=1)
    #: Une transition système peut proposer une action gouvernée, à date (S20-05).
    action: TransitionAction | None = None
    #: Une transition humaine peut être une tâche : un formulaire, une attestation (S20-06).
    task: TaskSpec | None = None
    #: Ce que la PLATEFORME fait en franchissant une transition système : ouvrir la PR du ticket, ou
    #: la fusionner. Avant, c'était le préfixe du nom de l'état visé (`pr_`, `merged`) : un renommage
    #: depuis la console changeait le comportement sans le dire (#175).
    does: Literal["open_pr", "merge_pr"] | None = None
    #: A system transition can open one child work item per element of a list and wait for them
    #: all (S38-12, ADR 0057).
    spawn: SpawnSpec | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("gates", mode="before")
    @classmethod
    def _normalize_gates(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        out: list[Any] = []
        for item in value:
            out.append({"name": item, "params": {}} if isinstance(item, str) else item)
        return out

    @model_validator(mode="after")
    def _actor_or_train(self) -> Transition:
        if self.via == "release_train":
            if self.by is not None:
                raise ValueError("a `via: release_train` transition has no `by`")
            if self.train is None:
                raise ValueError("a `via: release_train` transition needs `train: { env: … }`")
        elif self.by is None:
            raise ValueError("a transition needs `by: <actor>` or `via: release_train`")
        return self

    @property
    def key(self) -> str:
        """Identifiant stable d'une transition (pour les compteurs de tentatives)."""
        return self.id or f"{self.from_}->{self.to}"

    def gate_names(self) -> list[str]:
        return [g.name for g in self.gates]


class AgentStateDefaults(Strict):
    on_question: Identifier | None = None
    on_budget_exceeded: Identifier | None = None
    on_timeout: Identifier | None = None


class NeedsHumanDefaults(Strict):
    on_answer: Literal["resume"] = "resume"
    on_abandon: Identifier | None = None


class WorkflowDefaults(Strict):
    from_any_agent_state: AgentStateDefaults | None = None
    needs_human: NeedsHumanDefaults | None = None


class WorkflowBudget(Strict):
    """What a work item of this workflow may spend (S27-06, ADR 0047). It only tightens the policy's
    ticket budget; `on_exceeded` is where a work item goes when it is spent (before
    `defaults.from_any_agent_state.on_budget_exceeded`)."""

    ticket_usd: float | None = Field(default=None, gt=0)
    on_exceeded: Identifier | None = None


#: A MIME type an A2A caller may send (`text/plain`, `application/json`).
MediaType = Annotated[str, Field(pattern=r"^[a-z]+/[a-z0-9.+-]+$")]


class WorkflowA2a(Strict):
    """The workflow served as an A2A skill (ADR 0078). Absent, the workflow is not exposed: an agent
    outside Choregos sees only the workflows that opt in, on the card of their organisation."""

    skill: Identifier
    description: str = Field(min_length=1, max_length=1000)
    input_modes: list[MediaType] = Field(default_factory=lambda: ["text/plain"], min_length=1)


class Workflow(Strict):
    """Définition complète d'un workflow. La validation statique vit dans `choregos_core.dsl`."""

    apiVersion: Literal["choregos/v1"] = "choregos/v1"  # noqa: N815
    kind: Literal["Workflow"] = "Workflow"
    metadata: WorkflowMetadata
    actors: dict[str, Actor]
    states: dict[str, State]
    transitions: list[Transition]
    defaults: WorkflowDefaults | None = None
    budget: WorkflowBudget | None = None
    #: Served as an A2A skill (ADR 0078); absent: not exposed.
    a2a: WorkflowA2a | None = None
    #: Où commence le workflow. **Facultatif dans le YAML** : le parseur le remplit avec le premier
    #: état déclaré, qui reste la règle (§1.4). Mais il faut que ce soit ÉCRIT dans le document, et
    #: non déduit de l'ordre des clés — parce que cet ordre ne survit pas au stockage.
    #:
    #: PostgreSQL `jsonb` range les clés d'un objet par longueur puis par octets. Un workflow rangé
    #: dans la colonne `json` de `workflow_defs` en ressortait donc dans un autre ordre, et « le
    #: premier état déclaré » devenait « le nom d'état le plus court ». Constaté le 2026-09-27 sur
    #: un locataire réel : un ticket est né dans son état TERMINAL, l'interpréteur a fermé le
    #: ticket en 1,5 seconde, aucune étape n'a tourné, et rien nulle part n'a signalé d'anomalie.
    #: Les trois templates livrés y échappaient par chance — `inbox` y est le plus court.
    initial: str | None = None

    @property
    def initial_state(self) -> str:
        """Où commence le workflow : ce que le document déclare, sinon le premier état.

        Le repli sur l'ordre des clés reste, pour les documents écrits avant que le champ existe —
        mais il n'est fiable que si le document n'a pas traversé un `jsonb`. C'est pourquoi le
        parseur écrit toujours le champ, et pourquoi `workflow_model` préfère relire le YAML quand
        il manque.
        """
        return self.initial or next(iter(self.states))

    def terminal_states(self) -> list[str]:
        return [name for name, state in self.states.items() if state.terminal]

    def transitions_from(self, state: str) -> list[Transition]:
        """Transitions applicables depuis un état, `*agent` inclus."""
        agent_states = self.agent_driven_states()
        out: list[Transition] = []
        for t in self.transitions:
            if t.from_ == state or (t.from_ == AGENT_WILDCARD and state in agent_states):
                out.append(t)
        return out

    def agent_driven_states(self) -> set[str]:
        """States with at least one outgoing transition carried by an agent — or a job (ADR 0056): it
        runs in a run like an agent, and `*agent` (failure, cancellation) covers it."""
        result: set[str] = set()
        for t in self.transitions:
            if t.by is None or t.from_ == AGENT_WILDCARD:
                continue
            actor = self.actors.get(t.by)
            if isinstance(actor, RUN_ACTORS):
                result.add(t.from_)
        return result

    def actor_of(self, transition: Transition) -> Actor | None:
        return self.actors.get(transition.by) if transition.by else None


# ───────────────────────────── effets : écrits, ou déduits des noms (#175) ─────────────────────────────
#
# Un workflow écrit avant #175 portait ses effets dans des NOMS d'états : `pr_*` ouvrait la PR,
# `merged*` la fusionnait, `deployed_prod*` était la production. Ils se lisent encore — un workflow
# publié ne change pas de comportement, une histoire Temporal se rejoue —, mais le validateur avertit,
# et un renommage par la console les écrit d'abord (`does`, `production`).

PREFIXE_OUVERTURE_DE_PR = "pr_"
PREFIXE_FUSION = "merged"
PREFIXE_PRODUCTION = "deployed_prod"


def effet_implicite(etat: str) -> Literal["open_pr", "merge_pr"] | None:
    """L'effet qu'un NOM d'état portait avant #175 ; `None` s'il n'en porte pas."""
    if etat.startswith(PREFIXE_OUVERTURE_DE_PR):
        return "open_pr"
    if etat.startswith(PREFIXE_FUSION):
        return "merge_pr"
    return None


def effet_de_la_transition(transition: Transition) -> Literal["open_pr", "merge_pr"] | None:
    """Ce que la plateforme fait en franchissant une transition système : écrit, sinon déduit."""
    return transition.does or effet_implicite(transition.to)


def est_un_etat_de_production(nom: str, etat: State | None) -> bool:
    """Un état de production : écrit (`production: true`), sinon déduit du préfixe de son nom."""
    return bool(etat is not None and etat.production) or nom.startswith(PREFIXE_PRODUCTION)

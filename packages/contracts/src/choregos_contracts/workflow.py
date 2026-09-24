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


class WorkflowMetadata(Strict):
    name: Slug
    version: int = Field(ge=1)
    description: str | None = None
    extends: str | None = Field(default=None, pattern=r"^template:[a-z0-9-]+@[0-9]+$")


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


Actor = Annotated[AgentActor | HumanActor | SystemActor, Field(discriminator="type")]


class TrackerMapping(Strict):
    status: str | None = None
    label: str | None = None


class State(Strict):
    display: str = Field(min_length=1)
    tracker: TrackerMapping | None = None
    terminal: bool = False
    kind: StateKind = StateKind.WORK

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
    on_fail: Retry | None = None
    on_reject: Identifier | None = None
    on_changes_requested: Retry | None = None
    review: ReviewSpec | None = None
    timeout_hours: int | None = Field(default=None, ge=1)

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
                raise ValueError("une transition `via: release_train` ne porte pas `by`")
            if self.train is None:
                raise ValueError("une transition `via: release_train` exige `train: { env: … }`")
        elif self.by is None:
            raise ValueError("une transition exige `by: <acteur>` ou `via: release_train`")
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


class Workflow(Strict):
    """Définition complète d'un workflow. La validation statique vit dans `choregos_core.dsl`."""

    apiVersion: Literal["choregos/v1"] = "choregos/v1"  # noqa: N815
    kind: Literal["Workflow"] = "Workflow"
    metadata: WorkflowMetadata
    actors: dict[str, Actor]
    states: dict[str, State]
    transitions: list[Transition]
    defaults: WorkflowDefaults | None = None

    @property
    def initial_state(self) -> str:
        """Le premier état déclaré est l'état initial (règle §1.4)."""
        return next(iter(self.states))

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
        """États dont au moins une transition sortante est portée par un acteur agent."""
        result: set[str] = set()
        for t in self.transitions:
            if t.by is None or t.from_ == AGENT_WILDCARD:
                continue
            actor = self.actors.get(t.by)
            if isinstance(actor, AgentActor):
                result.add(t.from_)
        return result

    def actor_of(self, transition: Transition) -> Actor | None:
        return self.actors.get(transition.by) if transition.by else None

# SPDX-License-Identifier: Apache-2.0
"""Models of the workflow DSL (schemas/workflow.schema.json)."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import ActorType, StateKind, StateOutcome

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]

AGENT_WILDCARD = "*agent"


class Strict(BaseModel):
    """Base: unknown fields are refused, like the JSON Schemas."""

    model_config = ConfigDict(extra="forbid", frozen=False, use_enum_values=False)


CatalogueTag = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")]


class WorkflowCatalogueInfo(Strict):
    """How the workflow catalogue presents a workflow (ADR 0111); the engine ignores it."""

    title: str | None = Field(default=None, min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=400)
    tags: list[CatalogueTag] = Field(default_factory=list, max_length=12)


class WorkflowMetadata(Strict):
    name: Slug
    version: int = Field(ge=1)
    description: str | None = None
    extends: str | None = Field(default=None, pattern=r"^template:[a-z0-9-]+@[0-9]+$")
    #: The JSON Schema of the FIELDS of a ticket of this workflow (ADR 0031): the arrival date and the
    #: position of an onboarding, for example. Validated at the ticket's birth, passed to the agents.
    inputs: dict[str, Any] | None = None
    catalogue: WorkflowCatalogueInfo | None = None


#: An agent role. The package roles (`StageRole`) keep their meaning: the platform relies on
#: `implement`, `review` and `verify` for its measurements and for the cross review, but **a trade
#: names its own**: `sourcing`, `qualification`, `case_processing`.
#:
#: Before 2026-09-24 one had to write `role: custom` + `playbook: sourcing`: it worked, but the
#: board showed "custom" for every stage of a trade, and the evals did not know what it was about
#: (ADR 0012, limit no. 4). The playbook being already resolved by role name, opening the
#: enumeration was enough.
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
    #: A registry agent (ADR 0033): `slug` or `slug@version`; its instructions take precedence over
    #: the playbook.
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


#: A decision's output name: the answer is written under it, its probability under `<name>_confidence`.
DecisionName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")]
#: What a decision step may send: the work item's title, description, labels and type, its fields
#: (`fields.<name>`) and the last stage's outputs (`outputs.<name>`) — nothing else reaches the model.
DecisionState = Annotated[
    str,
    Field(
        pattern=r"^(title|description|labels|type|fields\.[a-z][a-z0-9_]{0,63}|outputs\.[a-z][a-z0-9_]{0,63})$"
    ),
]
DecisionInstructions = Annotated[str, Field(min_length=1, max_length=2000)]
DecisionLabel = Annotated[str, Field(min_length=1, max_length=200)]
#: The suffix of the output that carries a question's probability.
CONFIDENCE_SUFFIX = "_confidence"


class YesNoQuestion(Strict):
    """Answered `true` or `false`."""

    type: Literal["yes_no"] = "yes_no"
    instructions: DecisionInstructions


class ChoiceQuestion(Strict):
    """Answered by one of `options`, never by anything else."""

    type: Literal["choice"] = "choice"
    instructions: DecisionInstructions
    options: list[DecisionLabel] = Field(min_length=2, max_length=100)

    @field_validator("options")
    @classmethod
    def _distinct(cls, options: list[str]) -> list[str]:
        if len(set(options)) != len(options):
            raise ValueError("a choice's options must be distinct")
        return options


class ScoreQuestion(Strict):
    """Answered by one of `levels`, ordered lowest first."""

    type: Literal["score"] = "score"
    instructions: DecisionInstructions
    #: At most 10: the gateway's decision route (LiteLLM 1.105.5) refuses more criteria.
    levels: list[DecisionLabel] = Field(min_length=2, max_length=10)

    @field_validator("levels")
    @classmethod
    def _distinct(cls, levels: list[str]) -> list[str]:
        if len(set(levels)) != len(levels):
            raise ValueError("a score's levels must be distinct")
        return levels


DecisionQuestion = Annotated[YesNoQuestion | ChoiceQuestion | ScoreQuestion, Field(discriminator="type")]


class DecisionActor(Strict):
    """A decision step (ADR 0120): a decision model answers typed questions about the state the
    step names, through the project's gateway (`/v1/systemone`), in the orchestrator.

    A verdict of one word — finished? which team? risky? — does not need a pod, a sandbox and a model
    loop. Each question writes `<name>` and `<name>_confidence`, which `when` (ADR 0106) reads like an
    agent's outputs; the probability is the model's, and the decision ledger measures it.
    """

    type: Literal[ActorType.DECISION] = ActorType.DECISION
    role: Role
    #: A catalogue entry with the `decide` usage (ADR 0060), routed by the project's gateway.
    model: str = Field(default="platform/decide", min_length=1, max_length=200)
    state: list[DecisionState] = Field(min_length=1, max_length=32)
    questions: dict[DecisionName, DecisionQuestion] = Field(min_length=1, max_length=16)
    timeout_seconds: int = Field(default=15, ge=1, le=120)

    @field_validator("state")
    @classmethod
    def _state_distinct(cls, state: list[str]) -> list[str]:
        if len(set(state)) != len(state):
            raise ValueError("a decision's state names each source once")
        return state

    @property
    def outputs(self) -> list[str]:
        """The outputs the step writes: each question's answer, then its probability."""
        return [name for question in self.questions for name in (question, question + CONFIDENCE_SUFFIX)]


Actor = Annotated[
    AgentActor | HumanActor | SystemActor | JobActor | DecisionActor, Field(discriminator="type")
]
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
    #: How a terminal state ends the work item: `success` (the default), `declined` or `cancelled`.
    #: Only a terminal state may declare another outcome than the default.
    outcome: StateOutcome = StateOutcome.SUCCESS
    #: A PRODUCTION state: it is only reached through a release train (`via: release_train`).
    #: It used to be the prefix of its name (`deployed_prod`): a rename lifted the lock (#175).
    production: bool = False

    @model_validator(mode="after")
    def _coherent_kind(self) -> State:
        if self.terminal and self.kind is not StateKind.TERMINAL:
            object.__setattr__(self, "kind", StateKind.TERMINAL)
        if self.outcome is not StateOutcome.SUCCESS and self.kind is not StateKind.TERMINAL:
            raise ValueError(
                f"outcome `{self.outcome}` is only for a terminal state: a work item is not "
                "declined or cancelled while it can still move"
            )
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
    """An effect of an action (ADR 0035): its declared name (`connector.call`...), its parameters,
    rendered in isolated Jinja with `params` and `effects`, and what undoes it if the rest fails."""

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


#: `fields.<field>`, shifted or not: `fields.arrival_date - 10d`, `fields.departure_date + 2h`.
NOT_BEFORE = r"^fields\.[a-z_][a-z0-9_]{0,62}(\s*[+-]\s*[0-9]{1,4}\s*[dhm])?$"


class TransitionAction(Strict):
    """The governed action a SYSTEM transition proposes (ADR 0035, S20-05).

    `title`, `justification` and `params` are rendered in isolated Jinja with the ticket fields
    (`fields`) and the ticket (`work_item`); the effects, later, with `params`. The transition
    waits for `not_before` (a date taken from a field, re-armed when the field changes), proposes
    the action, and passes when it SUCCEEDED (`action_succeeded`).

    A human approval is required when a called operation requires it for this project, or when
    `approval` is declared: the workflow may ADD one, never remove one.
    """

    kind: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    title: str = Field(min_length=1, max_length=300)
    justification: str | None = Field(default=None, max_length=4000)
    params: dict[str, Any] = Field(default_factory=dict)
    effects: list[ActionEffectSpec] = Field(min_length=1, max_length=50)
    approval: ActionApprovalSpec | None = None
    not_before: str | None = Field(default=None, pattern=NOT_BEFORE)


class TaskSpec(Strict):
    """A TASK a person does so that the ticket moves on (S20-06): handing over a badge, unpacking
    a workstation. It fills in a form, an object JSON Schema whose properties are ticket fields:
    the values are poured into them, and the next action reads them, and attests what it did,
    word for word. The decision keeps both: this is the proof."""

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


#: One value an output condition compares with (S48-01).
ConditionScalar = str | bool | int | float


class OutputComparison(Strict):
    """Comparisons on one output of the last stage (S48-01, ADR 0106); every one given must hold."""

    equals: ConditionScalar | None = None
    in_: list[ConditionScalar] | None = Field(default=None, alias="in", min_length=1, max_length=50)
    not_in: list[ConditionScalar] | None = Field(default=None, min_length=1, max_length=50)
    gt: float | None = None
    gte: float | None = None
    lt: float | None = None
    lte: float | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _at_least_one(self) -> OutputComparison:
        if not self.model_dump(exclude_none=True):
            raise ValueError("an output comparison needs one of equals, in, not_in, gt, gte, lt, lte")
        return self


#: What one output must be: a value (equal), a list (one of), or comparisons.
OutputCondition = ConditionScalar | list[ConditionScalar] | OutputComparison

#: An output name, as the agent writes it.
OUTPUT_NAME = r"^[a-z][a-z0-9_]{0,62}$"


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
    #: Choose this transition only when the outputs of the work item's last stage match (S48-01,
    #: ADR 0106): the first transition from a state whose `when` holds is taken, one without `when`
    #: is the default. `{"outcome": "pr", "confidence": {"gte": 0.8}}`, `{"done": False}`.
    when: dict[str, OutputCondition] | None = Field(default=None, min_length=1, max_length=10)
    #: The type of each declared output (ADR 0049): `deck: pptx` is a file the agent writes in
    #: `.choregos/outputs/`; an output not listed is an inline value (markdown or JSON), as before.
    output_types: dict[str, str] = Field(default_factory=dict)
    on_fail: Retry | None = None
    on_reject: Identifier | None = None
    on_changes_requested: Retry | None = None
    review: ReviewSpec | None = None
    timeout_hours: int | None = Field(default=None, ge=1)
    #: A system transition can propose a governed action, dated (S20-05).
    action: TransitionAction | None = None
    #: A human transition can be a task: a form, an attestation (S20-06).
    task: TaskSpec | None = None
    #: What the PLATFORM does when crossing a system transition: open the ticket's PR, or merge
    #: it. It used to be the prefix of the target state's name (`pr_`, `merged`): a rename from
    #: the console changed the behaviour without saying so (#175).
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

    @field_validator("when")
    @classmethod
    def _output_names(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        for name in value or {}:
            if not re.fullmatch(OUTPUT_NAME, name):
                raise ValueError(f"`when` names `{name}`, which is not an output name ({OUTPUT_NAME})")
        return value

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
        """Stable identifier of a transition (for the attempt counters)."""
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
    """Complete definition of a workflow. Static validation lives in `choregos_core.dsl`."""

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
    #: Where the workflow starts. **Optional in the YAML**: the parser fills it with the first
    #: declared state, which remains the rule (§1.4). But it must be WRITTEN in the document, not
    #: deduced from the key order, because that order does not survive storage.
    #:
    #: PostgreSQL `jsonb` orders an object's keys by length, then by bytes. A workflow stored in
    #: the `json` column of `workflow_defs` therefore came out in another order, and "the first
    #: declared state" became "the shortest state name". Seen on 2026-09-27 on a real tenant: a
    #: ticket was born in its TERMINAL state, the interpreter closed the ticket in 1.5 seconds,
    #: no stage ran, and nothing anywhere reported an anomaly. The three shipped templates
    #: escaped by luck: `inbox` is the shortest there.
    initial: str | None = None

    @property
    def initial_state(self) -> str:
        """Where the workflow starts: what the document declares, otherwise the first state.

        The fallback on key order remains, for documents written before the field existed, but it
        is only reliable if the document has not been through a `jsonb`. That is why the parser
        always writes the field, and why `workflow_model` prefers to re-read the YAML when it
        is missing.
        """
        return self.initial or next(iter(self.states))

    def terminal_states(self) -> list[str]:
        return [name for name, state in self.states.items() if state.terminal]

    def transitions_from(self, state: str) -> list[Transition]:
        """Transitions applicable from a state, `*agent` included."""
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


# ───────────────────────────── effects: written, or deduced from names (#175) ─────────────────────────────
#
# A workflow written before #175 carried its effects in state NAMES: `pr_*` opened the PR,
# `merged*` merged it, `deployed_prod*` was production. They are still read (a published
# workflow does not change behaviour, a Temporal history replays), but the validator warns,
# and a rename through the console writes them first (`does`, `production`).

PR_OPEN_PREFIX = "pr_"
MERGE_PREFIX = "merged"
PRODUCTION_PREFIX = "deployed_prod"


def implicit_effect(state: str) -> Literal["open_pr", "merge_pr"] | None:
    """The effect a state NAME carried before #175; `None` if it carries none."""
    if state.startswith(PR_OPEN_PREFIX):
        return "open_pr"
    if state.startswith(MERGE_PREFIX):
        return "merge_pr"
    return None


def transition_effect(transition: Transition) -> Literal["open_pr", "merge_pr"] | None:
    """What the platform does when crossing a system transition: written, otherwise deduced."""
    return transition.does or implicit_effect(transition.to)


def is_production_state(name: str, state: State | None) -> bool:
    """A production state: written (`production: true`), otherwise deduced from its name prefix."""
    return bool(state is not None and state.production) or name.startswith(PRODUCTION_PREFIX)


PREFIXE_PRODUCTION = PRODUCTION_PREFIX  # lang: fr-ok alias:internal
effet_implicite = implicit_effect  # lang: fr-ok alias:internal
effet_de_la_transition = transition_effect  # lang: fr-ok alias:internal
est_un_etat_de_production = is_production_state  # lang: fr-ok alias:internal

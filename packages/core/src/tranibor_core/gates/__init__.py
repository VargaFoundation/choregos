# SPDX-License-Identifier: Apache-2.0
"""Gate registry: deterministic conditions carried by a transition.

A gate is **the** guarantee mechanism (docs/plan/01): it does not trust the agent, it
checks. Two families:

- **synchronous**: evaluable immediately from the `StageResult` and what the step declared
  (`evidence_present`, `outputs_present`, `output_schema`, `evidence_facts`, `numbers_measured`,
  `tool_called`…);
- **asynchronous**: they wait for an external event (`action_succeeded`, `flag_present`, `external`).

The core registers only the gates any trade can use. A trade's gates are registered by its plugin's
extension through the same `gate()` decorator, with the context provider whose facts they read
(ADR 0181): the twelve gates of software delivery that read a diff, a pipeline or a pull request
(`scope_respected`, `ci_green`…) are the `software-delivery` plugin's since round C2, under the names
stored workflows already carry.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from jsonschema import Draft202012Validator
from tranibor_contracts import StageResult

from ..errors import GateError


@dataclass(slots=True)
class GateContext:
    """Everything a gate needs to rule: what the core reads for any trade, and the facts the
    extensions' context providers read for their own gates (`facts`)."""

    result: StageResult | None = None
    flags: list[str] = field(default_factory=list)
    required_flag: str | None = None
    external_results: dict[str, bool] = field(default_factory=dict)
    # The outputs the transition declares (`outputs:`), read by `outputs_present`.
    expected_outputs: list[str] = field(default_factory=list)
    #: The catalogue tools this run CALLED (catalogue names), read from the cost
    #: ledger, never from what the agent says. `tool_called` uses it.
    tool_calls: list[str] = field(default_factory=list)
    #: The state of the governed action the transition proposed (S20-05), read from the database, never
    #: deduced: `action_succeeded` only passes on `succeeded`.
    action_status: str | None = None
    #: The item's documents: the outputs earlier steps declared, by name (`item.documents`).
    #: `numbers_measured` reads the measured ones there — a job's `metrics` — never the agent's text.
    documents: dict[str, Any] = field(default_factory=dict)
    #: The work item's fields (a campaign's period, costs, thresholds): `numbers_measured` reads
    #: them as the source `fields`, so a memo may restate what it was asked without inventing it.
    fields: dict[str, Any] = field(default_factory=dict)
    #: The documents of the work item that opened this one (`spawn:`, ADR 0057), `None` when it was
    #: not opened by a fan-out (created by hand). `numbers_measured` reads them as `parent.<doc>`:
    #: what the parent's steps MEASURED, which no field of the child can rewrite (S38-17).
    parent_documents: dict[str, Any] | None = None
    #: What the context providers read for this evaluation, keyed by provider (ADR 0181): a gate an
    #: extension registers declares the providers it reads (`gate(..., reads=...)`), and finds their
    #: facts here. A provider that was not called, or failed, has no key: its gates refuse. The diff
    #: and pipeline facts of software delivery are the `software-delivery` plugin's provider's (C2).
    facts: dict[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class GateOutcome:
    """A gate's verdict. `pending` = the gate waits for an external event."""

    name: str
    passed: bool
    pending: bool = False
    detail: str = ""
    annotations: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return not self.passed and not self.pending


GateFn = Callable[[GateContext, dict[str, Any]], GateOutcome]
#: What a declaration of a gate holds, in a sentence or a clause, from its params: the process view
#: reads it (`gate(..., summary=, detail=)`).
Describe = Callable[[Mapping[str, Any]], str | None]

#: The one value of a run-scoped gate's `over` parameter: the work item's whole change at the judged
#: commit, and nothing of a run (ADR 0171, decision 13).
OVER_WORK_ITEM = "work_item"


_REGISTRY: dict[str, GateFn] = {}
_ASYNC_GATES: set[str] = set()
_NEEDS: dict[str, frozenset[str]] = {}
#: gate -> the context providers whose facts it reads (`GateContext.facts`).
_READS: dict[str, frozenset[str]] = {}
#: gate -> the plugin whose extension registered it; a gate of the core has no entry.
_PLUGIN_OF: dict[str, str] = {}
#: The gates that read what a step wrote to the work item's workspace (a diff): a step whose
#: transition carries one writes there, and the process view says so (`writes_workspace`).
_WORKSPACE: set[str] = set()
#: The gates judged, unless a declaration says `over: work_item`, on what a RUN wrote (S22-11).
_RUN_SCOPED: set[str] = set()
_SUMMARY: dict[str, Describe] = {}
_DETAIL: dict[str, Describe] = {}
#: gate -> (the parameter it cannot do without, how the validator says it is missing).
_REQUIRES: dict[str, tuple[str, str]] = {}

#: The gates a plugin serves once its extension is installed, by name: what the core can say of a gate
#: it does not know, so that a workflow naming one is refused naming the plugin to install (ADR 0181).
#: The entry speaks only when the gate is not registered. A plugin not listed here is refused as an
#: unknown gate.
PLUGIN_GATES: Mapping[str, str] = dict.fromkeys(
    (
        "scope_respected",
        "diff_size_max",
        "no_secrets",
        "markdown_sections",
        "adr_number_free",
        "docs_updated",
        "coverage_delta_min",
        "ci_green",
        "review_approved",
        "scans_ok",
        "provenance_signed",
        "already_fixed_proven",
    ),
    "software-delivery",
)


def _describer(value: str | Describe | None) -> Describe | None:
    if value is None or callable(value):
        return value
    text = value
    return lambda _params: text


def gate(  # noqa: PLR0913 - the seam's declaration, keyword-only
    name: str,
    *,
    asynchronous: bool = False,
    needs: tuple[str, ...] = (),
    reads: tuple[str, ...] = (),
    plugin: str | None = None,
    workspace: bool = False,
    run_scoped: bool = False,
    summary: str | Describe | None = None,
    detail: Describe | None = None,
    requires: tuple[str, str] | None = None,
) -> Callable[[GateFn], GateFn]:
    """Register a gate; the core's own, or an extension's from its `choregos.plugins` entry point.

    `needs`: the connector capabilities the guarantee reads (ADR 0034). A workflow that sets it
    requires them of its project. `reads`: the context providers whose facts the gate reads in
    `GateContext.facts` (ADR 0181): the activity calls them before judging. `plugin`: the plugin whose
    extension registers the gate, named when the gate cannot be judged. `workspace`: the gate reads what
    a step wrote to the work item's workspace, so a step whose transition carries it writes there.
    `run_scoped`: the gate is judged on what the last run wrote, unless a declaration says `over:
    work_item`. `summary` and `detail`: what a declaration holds, for the process view. `requires`: the
    parameter without which the gate has nothing to check, and how to say it (`("paths", "`paths:` as a
    parameter of the guarantee")`): the validator refuses a declaration that lacks it."""

    def decorator(fn: GateFn) -> GateFn:
        _REGISTRY[name] = fn
        if asynchronous:
            _ASYNC_GATES.add(name)
        _NEEDS[name] = frozenset(needs)
        _READS[name] = frozenset(reads)
        if plugin is not None:
            _PLUGIN_OF[name] = plugin
        for registry, on in ((_WORKSPACE, workspace), (_RUN_SCOPED, run_scoped)):
            if on:
                registry.add(name)
            else:
                registry.discard(name)
        for described, value in ((_SUMMARY, _describer(summary)), (_DETAIL, detail)):
            if value is not None:
                described[name] = value
            else:
                described.pop(name, None)
        if requires is not None:
            _REQUIRES[name] = requires
        else:
            _REQUIRES.pop(name, None)
        return fn

    return decorator


def gate_needs(name: str) -> frozenset[str]:
    """The capabilities a guarantee reads; an unknown guarantee requires none."""
    return _NEEDS.get(name, frozenset())


def gate_reads(name: str) -> frozenset[str]:
    """The context providers a gate reads; an unknown gate or one of the core reads none."""
    return _READS.get(name, frozenset())


def gate_plugin(name: str) -> str | None:
    """The plugin that serves a gate: the one whose extension registered it, else the one `PLUGIN_GATES`
    names for it; `None` for a gate of the core, or one nobody knows."""
    return _PLUGIN_OF.get(name) or (PLUGIN_GATES.get(name) if name not in _REGISTRY else None)


def reads_the_workspace(name: str) -> bool:
    """Whether a gate reads what a step wrote to the work item's workspace (a diff)."""
    return name in _WORKSPACE


def workspace_gates() -> frozenset[str]:
    """The registered gates that read what a step wrote to the work item's workspace."""
    return frozenset(_WORKSPACE)


def is_run_scoped(name: str) -> bool:
    """Whether a gate is judged on a run's writes unless its declaration says `over: work_item`."""
    return name in _RUN_SCOPED


def judged_on_a_run(name: str, params: Mapping[str, Any] | None = None) -> bool:
    """Whether this declaration of a guarantee is judged on a run's writes: a run-scoped gate
    (`scope_respected`), unless it says `over: work_item`."""
    return name in _RUN_SCOPED and (params or {}).get("over") != OVER_WORK_ITEM


def gate_summary_of(name: str, params: Mapping[str, Any] | None = None) -> str | None:
    """What a registered gate says it checks, as this declaration reads it; `None` when it says nothing."""
    describe = _SUMMARY.get(name)
    return describe(params or {}) if describe is not None else None


def gate_detail_of(name: str, params: Mapping[str, Any] | None = None) -> str | None:
    """What a registered gate says it holds a step to, with its thresholds; `None` when it says nothing."""
    describe = _DETAIL.get(name)
    return describe(params or {}) if describe is not None else None


def gate_requires(name: str) -> tuple[str, str] | None:
    """The parameter a registered gate cannot do without, and how to say it; `None` when it needs none."""
    return _REQUIRES.get(name)


def known_gates() -> list[str]:
    return sorted(_REGISTRY)


def is_async_gate(name: str) -> bool:
    return name in _ASYNC_GATES


def unavailable(name: str) -> str | None:
    """Why this deployment cannot judge a gate, naming the plugin to install; `None` when it can.

    A gate nobody registered, and a registered gate one of whose context providers no extension
    declared: either would only fail when a work item reaches it, so a workflow naming one is refused
    when it is published (ADR 0181)."""
    if name not in _REGISTRY:
        plugin = PLUGIN_GATES.get(name)
        if plugin is not None:
            return (
                f"the gate `{name}` is served by the plugin `{plugin}`, whose extension is not installed "
                "in this deployment: install it, or remove the gate"
            )
        return f"unknown gate: {name}: no installed extension provides it (known: {', '.join(known_gates())})"
    missing = sorted(p for p in gate_reads(name) if p not in _PROVIDERS)
    if missing:
        owner = gate_plugin(name)
        by = f"the plugin `{owner}`" if owner else "the core"
        return (
            f"the gate `{name}` of {by} reads the facts of {', '.join(f'`{p}`' for p in missing)}, which no "
            "installed extension provides: install the extension that declares that context provider"
        )
    return None


def evaluate(name: str, ctx: GateContext, params: dict[str, Any] | None = None) -> GateOutcome:
    """Evaluate a registry gate. An unknown gate is a configuration error."""
    fn = _REGISTRY.get(name)
    if fn is None:
        raise GateError(unavailable(name) or f"unknown gate: {name}")
    return fn(ctx, params or {})


# ───────────────────────────── context providers ─────────────────────────────


@dataclass(slots=True, frozen=True)
class ProviderRequest:
    """What a context provider is given to read its facts, in the gate activity (ADR 0181, C0).

    `gates` holds the declarations of this transition that read the provider, `(name, params)`.
    `project` is the project as the worker holds it (its configuration, its policy engine and its
    connectors) and `session` the activity's database session: both opaque to the core, which neither
    reads nor types them. A provider reads; it never writes, and it is called again when the evaluation
    is replayed."""

    project_id: str
    work_item_id: str
    run_id: str | None = None
    result: StageResult | None = None
    gates: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    fields: Mapping[str, Any] = field(default_factory=dict)
    documents: Mapping[str, Any] = field(default_factory=dict)
    project: Any = None
    session: Any = None


@dataclass(slots=True, frozen=True)
class JudgedRequest:
    """What a provider's `judged` hook is given once the transition's gates have ruled (ADR 0181, C2).

    `request.gates` holds every declaration of the transition; `facts` is what the provider read for
    this evaluation, `None` when none of its gates was declared or it failed; `outcomes` the verdicts,
    in the order of the declarations."""

    request: ProviderRequest
    facts: Mapping[str, Any] | None
    outcomes: tuple[GateOutcome, ...]


ContextProvider = Callable[[ProviderRequest], Awaitable[Mapping[str, Any]]]
#: A provider's hook after the verdicts: what its plugin does with them in the outside system it reads
#: (marks a pull request's checks, records the commit judged), and the fields it adds to each verdict's
#: line in the run's journal (`{"head_sha": ...}`), or `None`.
Judged = Callable[[JudgedRequest], Awaitable[Mapping[str, Any] | None]]

_PROVIDERS: dict[str, ContextProvider] = {}
_PROVIDER_PLUGIN: dict[str, str] = {}
_JUDGED: dict[str, Judged] = {}


def context_provider(
    name: str, *, plugin: str, judged: Judged | None = None
) -> Callable[[ContextProvider], ContextProvider]:
    """Declare a context provider: an activity-side function that reads the facts gates of `plugin` judge
    (a diff, a pipeline's checks, a ticket's state), returned as a mapping filed under `name` in
    `GateContext.facts`. Two providers under one name are refused: the second would silently change
    what the first one's gates read.

    `judged`, when given, is called after every evaluation of a transition's gates, whichever gates it
    declared (`after_judgment`): the place where a plugin acts on the verdicts, since a provider only
    reads."""

    def decorator(fn: ContextProvider) -> ContextProvider:
        owner = _PROVIDER_PLUGIN.get(name)
        if name in _PROVIDERS and _PROVIDERS[name] is not fn:
            raise GateError(f"the context provider `{name}` is already declared by the plugin `{owner}`")
        _PROVIDERS[name] = fn
        _PROVIDER_PLUGIN[name] = plugin
        if judged is not None:
            _JUDGED[name] = judged
        else:
            _JUDGED.pop(name, None)
        return fn

    return decorator


async def after_judgment(
    request: ProviderRequest, facts: Mapping[str, Mapping[str, Any]], outcomes: Sequence[GateOutcome]
) -> dict[str, Any]:
    """Call each provider's `judged` hook, in order of name, and gather the fields they add to the
    verdicts' journal lines. A hook that raises fails the evaluation, which is retried: what it does
    (a check on a pull request, the commit a merge will name) is part of the verdict."""
    extra: dict[str, Any] = {}
    for name in sorted(_JUDGED):
        added = await _JUDGED[name](JudgedRequest(request, facts.get(name), tuple(outcomes)))
        extra.update(added or {})
    return extra


def forget(plugin: str) -> None:
    """Drop the gates and the context providers `plugin` registered: what a test does to see the
    deployment without that plugin's extension."""
    for name in [name for name, owner in _PLUGIN_OF.items() if owner == plugin]:
        for table in (_REGISTRY, _NEEDS, _READS, _PLUGIN_OF, _SUMMARY, _DETAIL, _REQUIRES):
            table.pop(name, None)
        _ASYNC_GATES.discard(name)
        _WORKSPACE.discard(name)
        _RUN_SCOPED.discard(name)
    for name in [name for name, owner in _PROVIDER_PLUGIN.items() if owner == plugin]:
        for providers in (_PROVIDERS, _PROVIDER_PLUGIN, _JUDGED):
            providers.pop(name, None)


def known_providers() -> list[str]:
    return sorted(_PROVIDERS)


def providers_read_by(names: list[str]) -> list[str]:
    """The providers the named gates read, each once, in order of name."""
    return sorted({provider for name in names for provider in gate_reads(name)})


async def provide_facts(
    declarations: Sequence[Mapping[str, Any]], request: ProviderRequest
) -> dict[str, Mapping[str, Any]]:
    """Call each context provider the declared gates read, once, and file what it returns by name.

    A provider that is not installed, raises, or returns something else than a mapping leaves no
    key: the gates that read it refuse on their own, rather than judge facts nobody read. It is
    logged, with the plugin that declared the provider."""
    import structlog

    log = structlog.get_logger("choregos.gates")
    facts: dict[str, Mapping[str, Any]] = {}
    for provider in providers_read_by([str(d["name"]) for d in declarations]):
        fn = _PROVIDERS.get(provider)
        if fn is None:
            log.warning("gate.provider_missing", provider=provider)
            continue
        reading = tuple(
            (str(d["name"]), dict(d.get("params") or {}))
            for d in declarations
            if provider in gate_reads(str(d["name"]))
        )
        try:
            found = await fn(replace(request, gates=reading))
        except Exception as error:  # a provider's failure is its gates' refusal, never the evaluation's
            log.warning(
                "gate.provider_failed",
                provider=provider,
                plugin=_PROVIDER_PLUGIN.get(provider),
                error=str(error),
            )
            continue
        if not isinstance(found, Mapping):
            log.warning("gate.provider_invalid", provider=provider, plugin=_PROVIDER_PLUGIN.get(provider))
            continue
        facts[provider] = dict(found)
    return facts


def matches_any(path: str, patterns: list[str]) -> bool:
    """A path is within the perimeter if a glob pattern covers it (`src/**` covers `src/a/b.py`)."""
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern):
            return True
        if pattern.endswith("/**") and (path == pattern[:-3] or path.startswith(pattern[:-2])):
            return True
        if pattern.endswith("**") and path.startswith(pattern[:-2]):
            return True
    return False


# ───────────────────────────── synchronous gates ─────────────────────────────


@gate("evidence_present")
def _evidence_present(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if ctx.result is None:
        return GateOutcome("evidence_present", False, detail="no stage result")
    ev = ctx.result.evidence
    missing: list[str] = []
    if ev.tests_passed is None:
        missing.append("tests_passed")
    if ev.tests_run is None:
        missing.append("tests_run")
    if params.get("require_lint", False) and ev.lint is None:
        missing.append("lint")
    if params.get("require_typecheck", False) and ev.typecheck is None:
        missing.append("typecheck")
    if ev.tests_passed is False:
        return GateOutcome("evidence_present", False, detail="tests are failing")
    return GateOutcome(
        "evidence_present",
        not missing,
        detail="evidence complete" if not missing else f"missing evidence: {', '.join(missing)}",
    )


@gate("outputs_in")
def _outputs_in(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The named outputs take an allowed value: `{values: {verdict: [approve]}}`.

    The engine does not read a review's verdict: a review that asked for changes passed
    `outputs_present` (the output exists) and the work item moved on. Here, the value counts.
    """
    permitted: dict[str, list[Any]] = dict(params.get("values") or {})
    if not permitted:
        return GateOutcome(
            "outputs_in", False, detail="no value required: `values:` missing on the guarantee"
        )
    if ctx.result is None:
        return GateOutcome("outputs_in", False, detail="no stage result")
    produced = ctx.result.outputs.model_dump(exclude_none=True)
    deviations = [
        f"{key} = {produced.get(key)!r} (allowed: {', '.join(str(v) for v in allowed_values)})"
        for key, allowed_values in permitted.items()
        if str(produced.get(key)) not in {str(v) for v in allowed_values}
    ]
    return GateOutcome(
        "outputs_in",
        not deviations,
        detail="outputs within the allowed values" if not deviations else "; ".join(deviations),
        annotations=deviations,
    )


def _heading_list(text: str) -> list[str]:
    """The Markdown headings of a text in reading order, without hashes or case: `## Risks` → `risks`."""
    return [
        line.lstrip("#").strip().lower()
        for line in text.splitlines()
        if line.startswith("#") and line.lstrip("#").startswith(" ")
    ]


@gate("output_sections")
def _output_sections(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The Markdown outputs of the stage carry their sections: `{outputs: {<name>: [<section>, ...]},
    patterns: {<name>: [<regex>, ...]}, ordered: false}`.

    `markdown_sections` (the software-delivery plugin's) reads the documents a branch adds, so it needs a
    repository. A memo that is an OUTPUT of the stage, with no repository behind it, is judged here: each
    section must be a heading of the text (any level, case-insensitive), and with `ordered: true` they
    must appear in the order given.
    """
    wanted: dict[str, Any] = dict(params.get("outputs") or {})
    if not wanted:
        return GateOutcome(
            "output_sections", False, detail="no output named: `outputs:` missing on the guarantee"
        )
    if ctx.result is None:
        return GateOutcome("output_sections", False, detail="no stage result")
    produced = ctx.result.outputs.model_dump(exclude_none=True)
    patterns: dict[str, Any] = dict(params.get("patterns") or {})
    ordered = bool(params.get("ordered", False))
    lacking: list[str] = []
    for name, sections in wanted.items():
        text = produced.get(name)
        if text is None:
            lacking.append(f"{name}: output absent")
            continue
        if not isinstance(text, str):
            lacking.append(f"{name}: output is not text ({type(text).__name__})")
            continue
        headings = _heading_list(text)
        required = [str(s) for s in sections or []]
        lacking += [f"{name}: section “{s}” missing" for s in required if s.lower() not in headings]
        if ordered:
            found = [s for s in required if s.lower() in headings]
            positions = [headings.index(s.lower()) for s in found]
            if positions != sorted(positions):
                lacking.append(f"{name}: sections out of order, expected {', '.join(required)}")
        lacking += [
            f"{name}: {m!r} not found" for m in patterns.get(name) or [] if not re.search(str(m), text)
        ]
    return GateOutcome(
        "output_sections",
        not lacking,
        detail=f"{len(wanted)} output(s) with their sections" if not lacking else "; ".join(lacking),
        annotations=lacking,
    )


@gate("outputs_present")
def _outputs_present(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The declared outputs of the stage exist and are not empty.

    The engine's generic guarantee: `evidence_present` speaks of tests and coverage,
    so of software. A stage that produces a list of candidates, a letter or an
    investigated file has no tests: it has OUTPUTS, which the transition names
    (`outputs:`) and which this one checks. Without it, a non-software workflow would have
    no mechanical guarantee, and the agent would have to be taken at its word.
    """
    required = [str(k) for k in (params.get("keys") or ctx.expected_outputs)]
    if not required:
        # Symmetry with `_without_diff`: a REQUESTED guarantee that has nothing to look at does
        # not pass, it refuses. Shipped on 2026-09-23, this one passed: the same defect
        # as the one just fixed on the diff guarantees, a day apart.
        # `allow_empty: true` stays possible for a transition whose outputs are
        # optional, but it must then be written.
        if params.get("allow_empty"):
            return GateOutcome("outputs_present", True, detail="no output declared (tolerated)")
        return GateOutcome(
            "outputs_present",
            False,
            detail="no output declared: the transition asks for this guarantee without saying "
            "what to produce (`outputs:`), or `allow_empty: true` to accept it",
        )
    if ctx.result is None:
        return GateOutcome("outputs_present", False, detail="no stage result")
    # `StageOutputs` tolerates extra fields (custom roles): that is what lets
    # a trade name its own outputs without touching the contract.
    produced = ctx.result.outputs.model_dump(exclude_none=True)
    # A file output (ADR 0049) is present when the runner stored it from `.choregos/outputs/`.
    produced.update(dict.fromkeys(ctx.result.artifacts.files, True))
    missing = [key for key in required if not produced.get(key)]
    return GateOutcome(
        "outputs_present",
        not missing,
        detail="outputs present" if not missing else f"missing outputs: {', '.join(missing)}",
        annotations=missing,
    )


#: The states of an action that settle the transition that proposed it. `in_doubt` (ADR 0149 §5) is
#: not one of them, although the action will not move again by itself: nobody knows whether its write
#: was made, and a failure would follow `on_fail`, whose retry proposes the action again under a new
#: attempt, a second delivery of the write. The transition waits, as it waits for an approval nobody
#: gave yet, until a person has looked.
ACTION_SETTLED = frozenset({"succeeded", "failed", "rejected"})


@gate("action_succeeded", asynchronous=True)
def _action_succeeded(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The transition's governed action SUCCEEDED: each of its effects done, confirmed (ADR 0035).

    Rejected, failed (and then compensated), it blocks; proposed, awaiting a decision, running or
    in doubt, it waits. A transition without an action has nothing to judge: the validator refuses it.
    """
    status = ctx.action_status
    if status is None or status not in ACTION_SETTLED:
        return GateOutcome("action_succeeded", False, pending=True, detail=f"action {status or 'to propose'}")
    if status == "succeeded":
        return GateOutcome("action_succeeded", True, detail="action succeeded")
    return GateOutcome("action_succeeded", False, detail=f"action {status}")


@gate("tool_called")
def _tool_called(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The tools the transition requires were called, in the ledger, not in the narrative.

    On the 2026-09-24 bench the sourcing playbook said "check the place with
    `verify_address` before anything else", the agent recorded `place_verified: true`, and the
    ledger had no call: ten catalogue listings, zero calls. A tool announced
    in a prompt is not a tool used. This guarantee reads the calls the
    platform counted itself (ADR 0014): the agent is not believed, it is measured.
    """
    required_tools = [str(t) for t in (params.get("tools") or [])]
    if not required_tools:
        return GateOutcome(
            "tool_called",
            False,
            detail="no tool required: the guarantee needs `tools: [...]` to have something to check",
        )
    called = set(ctx.tool_calls)
    lacking_tools = [t for t in required_tools if t not in called]
    return GateOutcome(
        "tool_called",
        not lacking_tools,
        detail=(
            f"tools called: {', '.join(required_tools)}"
            if not lacking_tools
            else f"tools required but never called (per the ledger): {', '.join(lacking_tools)}"
        ),
        annotations=lacking_tools,
    )


@gate("evidence_facts")
def _evidence_facts(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The evidence the trade named is there, and comparable if asked.

    `evidence_present` requires tests: that is the evidence of software. `outputs_present`
    checks that a stage produced its outputs, but an output is what the agent
    TELLS. Between the two lacked what an investigated file can offer that is measurable:
    named facts (`profiles_kept: 3`, `identity_document: true`, `days_elapsed: 12`)
    recorded in `evidence.facts` and checked here.

        gates:
          - name: evidence_facts
            params: { keys: [profiles_kept], min: { profiles_kept: 1 }, must_be_true: [need_complete] }

    `max` is the mirror bound (a ceiling: `max: { diverging_excess: 0 }`); like `min` it compares
    only real numbers, so a fact declared `true` never passes for a 1.

    `min` refuses a polite zero: an agent that finds nobody must say so by failing
    its stage, not by returning an empty piece of evidence that passes.
    """
    required = [str(k) for k in (params.get("keys") or [])]
    if not required:
        if params.get("allow_empty"):
            return GateOutcome("evidence_facts", True, detail="no fact required (tolerated)")
        return GateOutcome(
            "evidence_facts", False, detail="no fact required: `keys:` missing on the guarantee"
        )
    if ctx.result is None:
        return GateOutcome("evidence_facts", False, detail="no stage result")
    facts = ctx.result.evidence.facts or {}
    if params.get("measured_only"):
        # A fact the platform did not measure is a sentence (ADR 0045): with `measured_only`, a
        # threshold judges only what a command computed — a job's facts, the project's `dod.facts`.
        measured = set(ctx.result.evidence.measured or [])
        declared = [key for key in required if key in facts and f"facts.{key}" not in measured]
        if declared:
            return GateOutcome(
                "evidence_facts",
                False,
                detail=f"declared, not measured: {', '.join(declared)}",
                annotations=declared,
            )
    lacking_tools = [key for key in required if key not in facts]
    if lacking_tools:
        return GateOutcome(
            "evidence_facts",
            False,
            detail=f"missing facts: {', '.join(lacking_tools)}",
            annotations=lacking_tools,
        )
    # A boolean is an integer in Python: `identity_document: false` would pass a `min: 0`
    # without anyone wanting it. So only what is a real number is compared.
    failures = [
        f"{key} = {facts[key]} < {threshold}"
        for key, threshold in (params.get("min") or {}).items()
        if key in facts
        and isinstance(facts[key], int | float)
        and not isinstance(facts[key], bool)
        and float(facts[key]) < float(threshold)
    ]
    too_high = [
        f"{key} = {facts[key]} > {ceiling}"
        for key, ceiling in (params.get("max") or {}).items()
        if key in facts
        and isinstance(facts[key], int | float)
        and not isinstance(facts[key], bool)
        and float(facts[key]) > float(ceiling)
    ]
    failures += too_high
    # `must_be_true` and not `true`: in YAML, a `true:` key is read as the BOOLEAN
    # `True`, and the workflow is refused with a message about types. A parameter
    # name must not be a reserved word of the format that carries it.
    not_true = [key for key in (params.get("must_be_true") or []) if facts.get(key) is not True]
    if failures or not_true:
        detail = "; ".join(
            filter(None, [", ".join(failures), ("false: " + ", ".join(not_true)) if not_true else ""])
        )
        return GateOutcome("evidence_facts", False, detail=detail, annotations=failures + not_true)
    return GateOutcome("evidence_facts", True, detail=f"facts checked: {', '.join(sorted(required))}")


def _output_value(raw: Any) -> Any:
    """An inline output as data: a JSON output may arrive as a JSON string; anything else is kept."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


@gate("output_schema")
def _output_schema(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """Each named output matches its JSON Schema (2020-12): `{schemas: {strategies: {...}}}`.

    `outputs_present` says an output exists, `outputs_in` that a value is among a few; neither says
    a document has the shape the next step reads. A job reading strategies an agent wrote would fail
    on a missing key — or worse, compute on a silently ignored one. The schema is written in the
    workflow, so the shape is reviewed with it; the validator refuses a schema that is not one.
    """
    schemas: dict[str, Any] = dict(params.get("schemas") or {})
    if not schemas:
        return GateOutcome("output_schema", False, detail="no schema: `schemas:` missing on the guarantee")
    if ctx.result is None:
        return GateOutcome("output_schema", False, detail="no stage result")
    produced = ctx.result.outputs.model_dump(exclude_none=True)
    problems: list[str] = []
    for name, schema in sorted(schemas.items()):
        if name not in produced:
            problems.append(f"{name}: missing")
            continue
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(_output_value(produced[name])), key=lambda e: list(e.path))
        problems.extend(
            f"{name}{''.join(f'[{p!r}]' for p in error.path)}: {error.message[:200]}" for error in errors[:5]
        )
    matched = "outputs match their schema: " + ", ".join(sorted(schemas))
    return GateOutcome(
        "output_schema",
        not problems,
        detail="; ".join(problems) if problems else matched,
        annotations=problems,
    )


#: A number in prose: not inside a word (`s1`, `sma20`), thousands separators allowed, an optional %.
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:[,\u202f ]\d{3})*(?:\.\d+)?(?:\s?%)?(?![\w])")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
#: An identifier (`FA-0012`, `PO-2026-0042`, `CTR_17`, `ISO-9001`, `v2.3.1`): a letter first, then
#: letters and digits joined by `-`, `_`, `/` or `.`, holding a digit and a separator or a
#: letter-digit boundary. Its digits name a thing, they do not measure one.
_IDENTIFIER = re.compile(
    r"(?<![\w])(?=[A-Za-z][A-Za-z0-9]*(?:[-_/.][A-Za-z0-9]+)*)"
    r"(?=[A-Za-z0-9_/.-]*\d)"
    r"(?:[A-Za-z]+\d[A-Za-z0-9]*|[A-Za-z][A-Za-z0-9]*(?=[-_/.][A-Za-z0-9]))"
    r"(?:[-_/.][A-Za-z0-9]+)*"
)


def _numbers_in(value: Any, skip_identifiers: bool = False) -> list[float]:
    """Every number a document holds: its JSON numbers, and the numbers written in its strings."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [float(value)]
    if isinstance(value, str):
        return [n for _text, n in _cited(value, skip_identifiers)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers_in(v, skip_identifiers)]
    if isinstance(value, list):
        return [n for v in value for n in _numbers_in(v, skip_identifiers)]
    return []


def _cited(text: str, skip_identifiers: bool = False) -> list[tuple[str, float]]:
    """The numbers a text writes, as written and as values; a percentage is a fraction (12.5% = 0.125).

    ISO dates are removed first: they are not amounts. With `skip_identifiers`, identifiers (`FA-0012`,
    `CTR_17`, `v2.3.1`) are removed too; off by default, since `EUR-99999` is an identifier to this rule.
    """
    text = _ISO_DATE.sub(" ", text)
    if skip_identifiers:
        text = _IDENTIFIER.sub(" ", text)
    found = []
    for match in _NUMBER.finditer(text):
        written = match.group(0).strip()
        digits = re.sub(r"[,\u202f %]", "", written)
        try:
            value = float(digits)
        except ValueError:
            continue
        found.append((written, value / 100 if written.endswith("%") else value))
    return found


def _decimals(written: str) -> int:
    digits = written.rstrip("% ")
    return len(digits.split(".", 1)[1]) if "." in digits else 0


def _supported(written: str, value: float, measured: list[float], tolerance: float) -> bool:
    """A cited number is supported when a measured one rounds to it, or lies within the tolerance.

    Signs are ignored ("a 12% drawdown" quotes `max_drawdown: -0.12`); a percentage is compared as a
    fraction and as written, since a metric may be stored either way; a rounded number matches any
    measured value that rounds to it at the precision it is written with.
    """
    step = 0.5 * 10 ** -_decimals(written)
    if written.endswith("%"):
        # As written (12.5), against a measured fraction (0.125) or a metric already in percent (12.5).
        pairs = [(abs(value) * 100, abs(m) * scale) for m in measured for scale in (100.0, 1.0)]
    else:
        pairs = [(abs(value), abs(m)) for m in measured]
    return any(abs(c - t) <= step + 1e-9 or abs(c - t) <= tolerance * abs(t) for c, t in pairs)


def _quotes(written: str, value: float, number: float) -> bool:
    """A written number quotes `number` when it is that number, as a fraction or in percent; signs aside."""
    # `_cited` reads "35%" as 0.35; a metric may also be stored in percent (35.0).
    candidates = (abs(value), abs(value) * 100) if written.endswith("%") else (abs(value),)
    return any(abs(c - abs(number)) <= 1e-9 for c in candidates)


def _parent_source(ctx: GateContext, source: str) -> tuple[Any, str | None]:
    """`parent.<doc>[.<key>...]`: a document of the parent work item, walked down by keys.

    A key `$name` is the value of the child's own field `name` (`parent.metrics.products.$symbol`:
    the metrics of THIS product, not of its siblings). Returns `(value, None)`, or `(None, why)`.
    """
    if ctx.parent_documents is None:
        return None, "no parent work item (not opened by a fan-out)"
    name, *path = source.split(".")[1:] or [""]
    if name not in ctx.parent_documents:
        return None, f"the parent has no `{name}` document"
    value = _output_value(ctx.parent_documents[name])
    for step in path:
        key = step
        if step.startswith("$"):
            if step[1:] not in ctx.fields:
                return None, f"field `{step[1:]}` absent"
            key = str(ctx.fields[step[1:]])
        if not isinstance(value, dict) or key not in value:
            return None, f"`{key}` not in the parent's `{name}`"
        value = _output_value(value[key])
    return value, None


@gate("numbers_measured")
def _numbers_measured(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """Every number an agent writes in its outputs comes from a measured document.

    `{sources: [metrics, fields], outputs: [memo_markdown], tolerance: 0.005, allow: [252]}`.
    The analyst writing a research memo may round, express a fraction as a percentage, drop a sign;
    it may not cite a Sharpe ratio, a drawdown or a return the backtest did not compute. Years and
    integers below `ignore_below` (list numbering, "3 strategies") are not claims and are skipped;
    anything else needs a source. The sources are the item's documents that earlier steps declared
    — a job's measured `metrics` — and `fields`, the work item's own fields: the check reads what
    was computed or asked, never what was said. `fields` is editable by a person; a child opened by
    a fan-out checks `parent.<doc>[.<key>...]` instead (S38-17), the parent's measured document.

    `must_quote: [parent.metrics.thresholds]` (#824): every number of these sources must also be
    written somewhere in the outputs. A memo that calls a product "relevant" without the bar it
    passed reads, to whoever decides on it alone, as a pass against the campaign's usual bar —
    RESEARCH-4 on dev called a DSR of 0.03 "passing" when the bar had been lowered to 0. Each
    `must_quote` source is also a source of the check above. A number is quoted when it is written
    exactly (`0.95`, or `95%`), not merely rounded to: a "1" must not quote a bar of 0.95.

    `identifiers: skip` (S85-16) reads an identifier (`FA-0012`, `PO-2026-0042`, `CTR_17`, `v2.3.1`) as
    a reference, not an amount: its digits are read neither in the outputs nor in the sources. It is off by
    default (`identifiers: read`) because it is a hole a workflow opens knowingly: `EUR-99999` is an
    identifier to this rule, so an amount glued to letters by a separator is no longer checked.
    """
    # `identifiers: skip` (S85-16); the validator refuses any value but `read` and `skip`.
    skip = params.get("identifiers") == "skip"
    must_quote = [str(s) for s in (params.get("must_quote") or [])]
    sources = [str(s) for s in (params.get("sources") or [])]
    sources += [s for s in must_quote if s not in sources]
    if not sources:
        return GateOutcome("numbers_measured", False, detail="no source: `sources:` missing on the guarantee")
    if ctx.result is None:
        return GateOutcome("numbers_measured", False, detail="no stage result")
    documents = {**ctx.documents, "fields": ctx.fields}
    absent = []
    for source in sources:
        if source.startswith("parent."):
            value, why = _parent_source(ctx, source)
            if why is not None:
                absent.append(f"{source} ({why})")
            else:
                documents[source] = value
        elif source not in documents:
            absent.append(source)
    if absent:
        return GateOutcome(
            "numbers_measured",
            False,
            detail=f"source documents absent: {', '.join(absent)}",
            annotations=absent,
        )
    measured = [n for s in sources for n in _numbers_in(_output_value(documents[s]), skip)]
    measured += [float(n) for n in (params.get("allow") or [])]
    produced = ctx.result.outputs.model_dump(exclude_none=True)
    checked = [str(o) for o in (params.get("outputs") or ctx.expected_outputs)]
    missing = [o for o in checked if o not in produced]
    if not checked or missing:
        return GateOutcome(
            "numbers_measured",
            False,
            detail=f"outputs to check absent: {', '.join(missing) or '(none named)'}",
            annotations=missing,
        )
    tolerance = float(params.get("tolerance", 0.005))
    ignore_below = float(params.get("ignore_below", 10))
    unsupported: list[str] = []
    count = 0
    cited_everywhere: list[tuple[str, float]] = []
    for name in checked:
        raw = produced[name]
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        cited_everywhere += _cited(text, skip)
        for written, value in _cited(text, skip):
            is_integer = not written.endswith("%") and _decimals(written) == 0
            if is_integer and (abs(value) < ignore_below or 1900 <= value <= 2100):
                continue
            count += 1
            if not _supported(written, value, measured, tolerance):
                unsupported.append(f"{name}: {written}")
    unquoted = [
        f"{source}: {number:g}"
        for source in must_quote
        for number in _numbers_in(_output_value(documents[source]), skip)
        if not any(_quotes(written, value, number) for written, value in cited_everywhere)
    ]
    problems = []
    if unsupported:
        problems.append(
            f"{len(unsupported)} of {count} cited number(s) measured nowhere: " + ", ".join(unsupported[:20])
        )
    if unquoted:
        problems.append(f"{len(unquoted)} number(s) to quote written nowhere: " + ", ".join(unquoted))
    return GateOutcome(
        "numbers_measured",
        not problems,
        detail="; ".join(problems) or f"{count} cited number(s), all measured in {', '.join(sources)}",
        annotations=unsupported + unquoted,
    )


SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("AWS key", r"AKIA[0-9A-Z]{16}"),
    ("private key", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("GitHub token", r"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("Anthropic key", r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("OpenAI key", r"sk-(?:proj-)?[A-Za-z0-9]{32,}"),
    ("Slack token", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("JWT", r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)


def scan_secrets(text: str) -> list[str]:
    """A deliberately simple secret detector: it complements gitleaks, it does not replace it."""
    found: list[str] = []
    for label, pattern in SECRET_PATTERNS:
        if re.search(pattern, text):
            found.append(label)
    return found


# ───────────────────────────── asynchronous gates ─────────────────────────────


@gate("flag_present", asynchronous=True)
def _flag_present(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """Gate S9-06: a `risk: high` work item must name a feature flag that really exists."""
    name = params.get("name") or ctx.required_flag
    if not name:
        return GateOutcome("flag_present", False, detail="no feature flag named in the spec")
    ok = name in ctx.flags
    return GateOutcome(
        "flag_present",
        ok,
        detail=f"flag `{name}` " + ("present" if ok else "missing from the code / the flag provider"),
    )


@gate("external", asynchronous=True)
def _external(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    url = str(params.get("url", ""))
    if url not in ctx.external_results:
        return GateOutcome("external", False, pending=True, detail=f"waiting for {url}")
    ok = ctx.external_results[url]
    return GateOutcome("external", ok, detail=f"{url} → {'ok' if ok else 'ko'}")


# ───────────────────────────── a ticket already fixed (S22-47) ─────────────────────────────

#: The guarantee that a ticket's fix is already on the default branch (#539). A `system` transition
#: that carries it is an alternative exit of the agent step from the same state: the interpreter takes
#: it only after that step measured no change, never by default (`WorkflowEngine.already_fixed_exit`).
ALREADY_FIXED_GATE = "already_fixed_proven"

#: What an unproven "already fixed" says first: the reason a person reads on the escalation.
ALREADY_FIXED_UNPROVEN = (
    "the implementation changed nothing and the evidence does not prove the fix already exists"
)


def unchanged_run(result: StageResult | None) -> bool:
    """The runner MEASURED that the run changed nothing: `diff_files` and `diff_lines` measured by
    git (ADR 0045), both 0, and no commit. The agent's word that it changed nothing is not enough."""
    if result is None:
        return False
    evidence = result.evidence
    if not {"diff_files", "diff_lines"} <= set(evidence.measured or []):
        return False
    return not (evidence.diff_files or evidence.diff_lines or result.artifacts.commits)


__all__ = [
    "ALREADY_FIXED_GATE",
    "ALREADY_FIXED_UNPROVEN",
    "OVER_WORK_ITEM",
    "GateContext",
    "GateOutcome",
    "JudgedRequest",
    "ProviderRequest",
    "after_judgment",
    "context_provider",
    "evaluate",
    "gate",
    "gate_detail_of",
    "gate_needs",
    "gate_requires",
    "gate_summary_of",
    "is_async_gate",
    "is_run_scoped",
    "judged_on_a_run",
    "known_gates",
    "matches_any",
    "reads_the_workspace",
    "scan_secrets",
    "unchanged_run",
    "workspace_gates",
]

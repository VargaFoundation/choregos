# SPDX-License-Identifier: Apache-2.0
"""Gate registry: deterministic conditions carried by a transition.

A gate is **the** guarantee mechanism (docs/plan/01): it does not trust the agent, it
checks. Two families:

- **synchronous**: evaluable immediately from the `StageResult`, the diff and the policy
  (`scope_respected`, `evidence_present`, `diff_size_max`, `no_secrets`, `coverage_delta_min`,
  `adr_number_free`, `docs_updated`…);
- **asynchronous**: they wait for an external event (`ci_green`, `review_approved`,
  `scans_ok`, `provenance_signed`, `flag_present`, `external`).
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from choregos_contracts import StageResult
from jsonschema import Draft202012Validator

from ..errors import GateError


@dataclass(slots=True)
class GateContext:
    """Everything a synchronous gate needs to rule."""

    result: StageResult | None = None
    changed_files: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    allowed_paths: list[str] = field(default_factory=list)
    secrets_found: list[str] = field(default_factory=list)
    ci_status: str | None = None
    review_state: str | None = None
    scans: dict[str, str] = field(default_factory=dict)
    signed: bool | None = None
    flags: list[str] = field(default_factory=list)
    required_flag: str | None = None
    external_results: dict[str, bool] = field(default_factory=dict)
    # The outputs the transition declares (`outputs:`), read by `outputs_present`.
    expected_outputs: list[str] = field(default_factory=list)
    #: The catalogue tools this run CALLED (catalogue names), read from the cost
    #: ledger, never from what the agent says. `tool_called` uses it.
    tool_calls: list[str] = field(default_factory=list)
    #: Could the diff be obtained? False when the SCM connector cannot compare
    #: (fake, outage, unreachable repository). A guarantee that rests on the diff must then
    #: not rule: without a diff, `scope_respected` would declare the perimeter respected and
    #: `no_secrets` the absence of secrets, having looked at nothing.
    diff_available: bool = True
    #: The state of the governed action the transition proposed (S20-05), read from the database, never
    #: deduced: `action_succeeded` only passes on `succeeded`.
    action_status: str | None = None
    #: The text the branch ADDS, file by file: the `+` lines of the cumulated diff (a
    #: new file is entirely added). `markdown_sections` reads what is written, not what
    #: the agent says it wrote.
    added_text: dict[str, str] = field(default_factory=dict)
    #: The paths present on the DEFAULT branch, under the folder the guarantee reads
    #: (`adr_number_free`); `None` when the SCM connector could not list them.
    default_branch_files: list[str] | None = None
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
    #: Does the work item's branch exist on the SCM (S22-47)? `None` when nobody read it, or the
    #: SCM could not answer: `already_fixed_proven` then refuses rather than assume.
    branch_pushed: bool | None = None


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

#: The gates that read the work item's diff. A step whose transition carries one of them writes to the
#: work item's branch: the orchestrator fetches the diff for them, and the process view says so
#: (`writes_branch`). One place: a new diff gate is added here, nowhere else.
DIFF_GATES: frozenset[str] = frozenset(
    {
        "scope_respected",
        "diff_size_max",
        "no_secrets",
        "markdown_sections",
        "adr_number_free",
        "docs_updated",
    }
)

_REGISTRY: dict[str, GateFn] = {}
_ASYNC_GATES: set[str] = set()
_NEEDS: dict[str, frozenset[str]] = {}


def gate(name: str, *, asynchronous: bool = False, needs: tuple[str, ...] = ()) -> Callable[[GateFn], GateFn]:
    """`needs`: the connector capabilities the guarantee reads (ADR 0034). A diff comes from the
    `scm`, a pipeline status from the `ci`. A workflow that sets it requires them of its project."""

    def decorator(fn: GateFn) -> GateFn:
        _REGISTRY[name] = fn
        if asynchronous:
            _ASYNC_GATES.add(name)
        _NEEDS[name] = frozenset(needs)
        return fn

    return decorator


def gate_needs(name: str) -> frozenset[str]:
    """The capabilities a guarantee reads; an unknown guarantee requires none."""
    return _NEEDS.get(name, frozenset())


def known_gates() -> list[str]:
    return sorted(_REGISTRY)


def is_async_gate(name: str) -> bool:
    return name in _ASYNC_GATES


def evaluate(name: str, ctx: GateContext, params: dict[str, Any] | None = None) -> GateOutcome:
    """Evaluate a registry gate. An unknown gate is a configuration error."""
    fn = _REGISTRY.get(name)
    if fn is None:
        raise GateError(f"unknown gate: {name} (known: {', '.join(known_gates())})")
    return fn(ctx, params or {})


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


def _without_diff(name: str) -> GateOutcome:
    return GateOutcome(
        name,
        False,
        detail="diff unavailable: this guarantee could not be evaluated (SCM connector)",
    )


@gate("scope_respected", needs=("scm",))
def _scope_respected(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if not ctx.diff_available:
        return _without_diff("scope_respected")
    allowed = params.get("paths") or ctx.allowed_paths
    if not allowed:
        return GateOutcome("scope_respected", True, detail="no allowed paths declared")
    out = [p for p in ctx.changed_files if not matches_any(p, list(allowed))]
    return GateOutcome(
        "scope_respected",
        not out,
        detail="within the allowed paths" if not out else f"{len(out)} file(s) outside the allowed paths",
        annotations=out,
    )


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


def _headings(text: str) -> set[str]:
    """The Markdown headings of a text, without hashes or case: `## Decision Outcome` → `decision outcome`."""
    return {
        line.lstrip("#").strip().lower()
        for line in text.splitlines()
        if line.startswith("#") and line.lstrip("#").startswith(" ")
    }


@gate("markdown_sections", needs=("scm",))
def _markdown_sections(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The documents the branch adds carry their sections: `{paths, sections, patterns,
    min_files}`. An ADR in the MADR 4 format must say its context, its options and its decision, and
    say it IN the file, read in the diff, not in the agent's summary."""
    if not ctx.diff_available:
        return _without_diff("markdown_sections")
    paths = [str(c) for c in params.get("paths") or []]
    if not paths:
        return GateOutcome("markdown_sections", False, detail="no path: `paths:` missing on the guarantee")
    files = {path: text for path, text in ctx.added_text.items() if matches_any(path, paths)}
    minimum = int(params.get("min_files", 1))
    if len(files) < minimum:
        return GateOutcome(
            "markdown_sections",
            False,
            detail=f"{len(files)} file(s) matching {', '.join(paths)} in the change, {minimum} expected",
        )
    lacking = []
    for path, text in sorted(files.items()):
        titres = _headings(text)
        lacking += [
            f"{path}: section “{s}” missing"
            for s in params.get("sections") or []
            if str(s).lower() not in titres
        ]
        lacking += [
            f"{path}: {m!r} not found" for m in params.get("patterns") or [] if not re.search(str(m), text)
        ]
    return GateOutcome(
        "markdown_sections",
        not lacking,
        detail=f"{len(files)} document(s) with their sections" if not lacking else "; ".join(lacking),
        annotations=lacking,
    )


#: The files that count as documentation for `docs_updated` (`*` matches `/` under `fnmatch`).
DOCS_PATHS = ("docs/**", "*.md", "*.mdx", "*.rst", "*.adoc", "CHANGELOG*")
#: The files whose change needs no documentation: tests, lockfiles, CI.
NO_DOCS_NEEDED = (
    "tests/**",
    "test/**",
    "*/tests/*",
    "*test_*",
    "*_test.*",
    "*.test.*",
    "*.spec.*",
    "*/__tests__/*",
    ".github/**",
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
)


@gate("docs_updated", needs=("scm",))
def _docs_updated(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The work item's whole branch updates its documentation when it changes anything else.

    Params: `docs` (globs of documentation files), `ignore` (globs that need no documentation:
    tests, lockfiles, CI), `min_files` (documentation files to update, default 1). A documentation
    file counts when the diff adds text to it, or changes it with no patch (the forge omits big
    patches); a file that is only deleted does not. A changelog line is enough. The gate reads the
    branch diff, not the agent's report: it never takes the writer's word that nothing needed saying.
    """
    if not ctx.diff_available:
        return _without_diff("docs_updated")
    docs = [str(p) for p in params.get("docs") or DOCS_PATHS]
    ignore = [str(p) for p in params.get("ignore") or NO_DOCS_NEEDED]
    minimum = int(params.get("min_files", 1))
    needing = [p for p in ctx.changed_files if not matches_any(p, docs) and not matches_any(p, ignore)]
    if not needing:
        return GateOutcome("docs_updated", True, detail="nothing outside documentation and tests changed")
    updated = sorted(
        p
        for p in ctx.changed_files
        if matches_any(p, docs) and (p not in ctx.added_text or ctx.added_text[p].strip())
    )
    if len(updated) >= minimum:
        return GateOutcome(
            "docs_updated",
            True,
            detail=f"{len(updated)} documentation file(s) updated: {', '.join(updated[:5])}",
        )
    return GateOutcome(
        "docs_updated",
        False,
        detail=f"{len(needing)} file(s) changed outside documentation and tests, and "
        f"{len(updated)} documentation file(s) updated, {minimum} expected ({', '.join(docs)})",
        annotations=needing[:20],
    )


#: The default pattern of a numbered ADR: `docs/adr/0042-title.md`.
ADR_PATTERN = "docs/adr/[0-9][0-9][0-9][0-9]-*.md"


def _number(path: str) -> int | None:
    """The number at the head of the file name: `docs/adr/0042-rls.md` → 42."""
    found = re.match(r"(\d+)-", path.rsplit("/", 1)[-1])
    return int(found.group(1)) if found else None


@gate("adr_number_free", needs=("scm",))
def _adr_number_free(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The number of the ADR the branch adds is not already taken on the default branch (#287).

    The architect numbers "the largest existing plus one", read at the moment of writing: two studies
    that run together take the same number, and the second would merge a duplicate. Nothing
    reserves a number; this guarantee, set at the merge, refuses the duplicate at the last useful moment.
    A path already present as is on the default branch is not an addition: it is the same
    file (or an add conflict the forge refuses by itself).
    """
    if not ctx.diff_available:
        return _without_diff("adr_number_free")
    pattern = str(params.get("pattern") or ADR_PATTERN)
    if ctx.default_branch_files is None:
        if not any(fnmatch.fnmatch(p, pattern) for p in ctx.changed_files):
            return GateOutcome("adr_number_free", True, detail="no numbered record added by the change")
        return GateOutcome(
            "adr_number_free",
            False,
            detail="default branch unavailable: the numbers already taken could not be listed "
            "(SCM connector)",
        )
    existing = sorted(p for p in ctx.default_branch_files if fnmatch.fnmatch(p, pattern))
    added = [p for p in ctx.changed_files if fnmatch.fnmatch(p, pattern) and p not in existing]
    if not added:
        return GateOutcome("adr_number_free", True, detail="no numbered record added by the change")
    taken: dict[int, list[str]] = {}
    for path in existing:
        number = _number(path)
        if number is not None:
            taken.setdefault(number, []).append(path)
    duplicates = [
        (path, number, taken[number])
        for path in sorted(added)
        if (number := _number(path)) is not None and number in taken
    ]
    if not duplicates:
        return GateOutcome(
            "adr_number_free",
            True,
            detail=f"number free on the default branch: {', '.join(sorted(added))}",
        )
    free_number = max(taken) + 1
    return GateOutcome(
        "adr_number_free",
        False,
        detail="; ".join(
            f"{path}: number {number:04d} is already taken on the default branch by {', '.join(others)}"
            for path, number, others in duplicates
        )
        + f" — renumber it (next free number: {free_number:04d})",
        annotations=[path for path, _, _ in duplicates],
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


#: The states of an action that will not move again.
ACTION_SETTLED = frozenset({"succeeded", "failed", "rejected"})


@gate("action_succeeded", asynchronous=True)
def _action_succeeded(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The transition's governed action SUCCEEDED: each of its effects done, confirmed (ADR 0035).

    Rejected, failed (and then compensated), it blocks; proposed, awaiting a decision or
    running, it waits. A transition without an action has nothing to judge: the validator refuses it.
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


def _numbers_in(value: Any) -> list[float]:
    """Every number a document holds: its JSON numbers, and the numbers written in its strings."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [float(value)]
    if isinstance(value, str):
        return [n for _text, n in _cited(value)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers_in(v)]
    if isinstance(value, list):
        return [n for v in value for n in _numbers_in(v)]
    return []


def _cited(text: str) -> list[tuple[str, float]]:
    """The numbers a text writes, as written and as values; a percentage is a fraction (12.5% = 0.125)."""
    found = []
    for match in _NUMBER.finditer(_ISO_DATE.sub(" ", text)):
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
    """
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
    measured = [n for s in sources for n in _numbers_in(_output_value(documents[s]))]
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
        cited_everywhere += _cited(text)
        for written, value in _cited(text):
            is_integer = not written.endswith("%") and _decimals(written) == 0
            if is_integer and (abs(value) < ignore_below or 1900 <= value <= 2100):
                continue
            count += 1
            if not _supported(written, value, measured, tolerance):
                unsupported.append(f"{name}: {written}")
    unquoted = [
        f"{source}: {number:g}"
        for source in must_quote
        for number in _numbers_in(_output_value(documents[source]))
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


@gate("diff_size_max", needs=("scm",))
def _diff_size_max(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if not ctx.diff_available:
        return _without_diff("diff_size_max")
    max_files = int(params.get("files", params.get("n", 60)))
    max_lines = int(params.get("lines", 100_000))
    files = len(ctx.changed_files)
    lines = ctx.additions + ctx.deletions
    if files > max_files:
        return GateOutcome("diff_size_max", False, detail=f"{files} files changed > {max_files}")
    if lines > max_lines:
        return GateOutcome("diff_size_max", False, detail=f"{lines} lines changed > {max_lines}")
    return GateOutcome("diff_size_max", True, detail=f"{files} files / {lines} lines")


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


@gate("no_secrets", needs=("scm",))
def _no_secrets(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if not ctx.diff_available:
        return _without_diff("no_secrets")
    found = ctx.secrets_found
    return GateOutcome(
        "no_secrets",
        not found,
        detail="no secret detected" if not found else f"secrets detected: {', '.join(found)}",
        annotations=found,
    )


# The coverage the runner MEASURES in the cloned repository: a repository, not a CI (S21-23).
@gate("coverage_delta_min", needs=("scm",))
def _coverage_delta_min(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    threshold = float(params.get("x", params.get("min", 0.0)))
    if ctx.result is None or ctx.result.evidence.coverage_delta is None:
        return GateOutcome("coverage_delta_min", False, detail="coverage delta unknown")
    delta = ctx.result.evidence.coverage_delta
    return GateOutcome(
        "coverage_delta_min",
        delta >= threshold,
        detail=f"coverage {delta:+.2f} (threshold {threshold:+.2f})",
    )


# ───────────────────────────── asynchronous gates ─────────────────────────────


# `ci_green` and `scans_ok` read the PR's checks THROUGH THE REPOSITORY (`scm.get_pr`, in
# `activities/gates.py`), GitHub Actions as well as Tekton. They declared a `ci` that nothing read:
# a project on GitHub Actions was asked for a Tekton it does not have (S21-23).
@gate("ci_green", asynchronous=True, needs=("scm",))
def _ci_green(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    # `pending` is what `checks_conclusion` returns while a check runs: taking it for a
    # failure escalated a work item whose CI, green 13 s later, had not finished (#5 of the
    # dev tenant, 09/10, S22-14).
    if ctx.ci_status in (None, "pending"):
        return GateOutcome("ci_green", False, pending=True, detail="CI pending")
    ok = ctx.ci_status in {"success", "succeeded", "neutral"}
    return GateOutcome("ci_green", ok, detail=f"CI {ctx.ci_status}")


@gate("review_approved", asynchronous=True, needs=("scm",))
def _review_approved(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if ctx.review_state is None:
        return GateOutcome("review_approved", False, pending=True, detail="review pending")
    ok = ctx.review_state == "approved"
    return GateOutcome("review_approved", ok, detail=f"review {ctx.review_state}")


@gate("scans_ok", asynchronous=True, needs=("scm",))
def _scans_ok(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    required = list(params.get("scanners") or sorted(ctx.scans) or ["semgrep", "trivy", "gitleaks"])
    if not ctx.scans:
        if ctx.ci_status in {"success", "failure", "neutral"}:
            return GateOutcome(
                "scans_ok",
                False,
                detail="no security scan declared on the PR: add semgrep/trivy/gitleaks "
                "to the CI, or remove the `scans_ok` gate from the workflow",
            )
        return GateOutcome("scans_ok", False, pending=True, detail="scans pending")
    failed = [name for name in required if ctx.scans.get(name, "pending") not in {"ok", "passed", "skipped"}]
    pending = [name for name in required if ctx.scans.get(name) is None]
    if pending:
        return GateOutcome("scans_ok", False, pending=True, detail=f"scans pending: {', '.join(pending)}")
    return GateOutcome(
        "scans_ok", not failed, detail="scans OK" if not failed else f"failed: {', '.join(failed)}"
    )


# The provenance is read on the PR, like the scans: a check whose name contains `provenance`
# (`attest-build-provenance`, `slsa-provenance`…). Nothing filled in `signed`: the guarantee
# waited forever, and an `advanced` work item never merged (#283).
@gate("provenance_signed", asynchronous=True, needs=("scm",))
def _provenance_signed(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if ctx.signed is None:
        if ctx.ci_status in {"success", "failure", "neutral", "succeeded"}:
            return GateOutcome(
                "provenance_signed",
                False,
                detail="no provenance check on the pull request: add one to the CI (a check named "
                "`provenance`), or remove the `provenance_signed` guarantee from the workflow",
            )
        return GateOutcome("provenance_signed", False, pending=True, detail="provenance pending")
    return GateOutcome("provenance_signed", ctx.signed, detail="signed" if ctx.signed else "not signed")


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


@gate(ALREADY_FIXED_GATE, needs=("scm",))
def _already_fixed_proven(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The ticket's fix is already on the default branch, on measured evidence only (#539).

    The run changed nothing (`unchanged_run`), pushed no branch (read from the SCM), and the tests
    pass on that unchanged checkout — which is the default branch: the runner ran them, at least one,
    none failed (`tests_run` and `tests_failed` in `evidence.measured`). Anything missing refuses,
    and says what is missing."""
    name = ALREADY_FIXED_GATE
    result = ctx.result
    if result is None:
        return GateOutcome(name, False, detail="no stage result")
    evidence = result.evidence
    measured = set(evidence.measured or [])
    if not unchanged_run(result):
        return GateOutcome(name, False, detail="the run did not measure an empty change")
    if ctx.branch_pushed is None:
        return GateOutcome(name, False, detail="whether a branch was pushed could not be read from the SCM")
    if ctx.branch_pushed:
        return GateOutcome(name, False, detail="a work branch was pushed")
    if not {"tests_run", "tests_failed"} <= measured:
        return GateOutcome(name, False, detail="the platform did not run the tests (none measured)")
    if not evidence.tests_run:
        return GateOutcome(name, False, detail="no test ran on the default branch")
    if evidence.tests_failed or evidence.tests_passed is False:
        failed = evidence.tests_failed or "some"
        return GateOutcome(name, False, detail=f"{failed} test(s) fail on the default branch")
    return GateOutcome(
        name,
        True,
        detail=(
            f"nothing to change (0 files, 0 lines, no commit, no branch) and {evidence.tests_run} "
            "test(s) pass on the default branch, 0 failed — measured by the platform"
        ),
    )


__all__ = [
    "ALREADY_FIXED_GATE",
    "ALREADY_FIXED_UNPROVEN",
    "DIFF_GATES",
    "GateContext",
    "GateOutcome",
    "evaluate",
    "gate",
    "gate_needs",
    "is_async_gate",
    "known_gates",
    "matches_any",
    "scan_secrets",
    "unchanged_run",
]

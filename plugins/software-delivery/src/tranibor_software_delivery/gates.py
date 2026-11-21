# SPDX-License-Identifier: Apache-2.0
"""The twelve gates of software delivery (ADR 0181, round C2), registered by the plugin's extension.

They read a diff, a pipeline's checks, a pull request's review or the branch on the SCM: the facts of
the plugin's context provider `delivery` (`tranibor_software_delivery.facts`), filed in
`GateContext.facts["delivery"]`. Their names are the ones stored workflows carry, unchanged: they were
the core's until round C2, and the core still knows them by name (`PLUGIN_GATES`), so that a workflow
naming one where this extension is not installed is refused at publication, naming the plugin.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from tranibor_core.gates import (
    ALREADY_FIXED_GATE,
    OVER_WORK_ITEM,
    Describe,
    GateContext,
    GateFn,
    GateOutcome,
    gate,
    judged_on_a_run,
    matches_any,
    unchanged_run,
)

from .names import NAME

#: The context provider whose facts the gates read, and the key of `GateContext.facts` they are under.
PROVIDER = "delivery"

#: The gates that read the work item's diff. A step whose transition carries one of them writes to the
#: work item's branch: the provider reads the diff for them, and the process view says so
#: (`writes_workspace`). One place: a new diff gate is added here, nowhere else.
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

#: The diff gates judged, unless their declaration says otherwise, on what a RUN wrote since the
#: commit where it began (S22-11), against the paths that run may write: the others read the work
#: item's whole diff at the judged commit. On a `system` transition the run is the last step's, or
#: none, so such a declaration holds nothing on what is merged (ADR 0171). `judged_on_a_run` reads a
#: declaration; a name alone does not say.
RUN_SCOPED_GATES: frozenset[str] = frozenset({"scope_respected"})

#: The gates that wait for the pull request's checks or review.
PULL_REQUEST_GATES: frozenset[str] = frozenset(
    {"ci_green", "review_approved", "scans_ok", "provenance_signed"}
)


@dataclass(slots=True, frozen=True)
class DeliveryFacts:
    """What the `delivery` provider read for one evaluation: the diff of the work item's branch at the
    judged commit, the checks and review of its pull request on that commit, and the branch itself.

    A fact nobody could read keeps its default: no diff (`diff_available` false, so the diff gates
    refuse rather than judge nothing), no CI status or review (the pull request's gates wait), no
    branch read (`already_fixed_proven` refuses)."""

    #: Could the diff be obtained? False when the SCM connector cannot compare (fake, outage,
    #: unreachable repository). A guarantee that rests on the diff must then not rule: without a
    #: diff, `scope_respected` would declare the perimeter respected and `no_secrets` the absence of
    #: secrets, having looked at nothing.
    diff_available: bool = True
    #: The files of the work item's whole diff at the judged commit.
    changed_files: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    #: What the last run wrote since the commit where it began (S22-11); `None` when unread, and the
    #: whole diff then holds.
    run_changed_files: list[str] | None = None
    #: The paths the run, else the work item, may write.
    allowed_paths: list[str] = field(default_factory=list)
    #: The paths the project's policy forbids (`scope.deny_paths`): `scope_respected` fails on a change
    #: under one of them, whatever the allowed paths say.
    denied_paths: list[str] = field(default_factory=list)
    secrets_found: list[str] = field(default_factory=list)
    #: The text the branch ADDS, file by file: the `+` lines of the cumulated diff (a new file is
    #: entirely added). `markdown_sections` reads what is written, not what the agent says it wrote.
    added_text: dict[str, str] = field(default_factory=dict)
    #: The paths present on the DEFAULT branch, under the folders the `adr_number_free` declarations
    #: read; `None` when the SCM connector could not list one of them.
    default_branch_files: list[str] | None = None
    #: The project's policy bounds on a diff, `(files, lines)` (`scope.max_diff_*`).
    max_diff: tuple[int, int] | None = None
    ci_status: str | None = None
    review_state: str | None = None
    scans: dict[str, str] = field(default_factory=dict)
    signed: bool | None = None
    #: Does the work item's branch exist on the SCM (S22-47)? `None` when nobody read it, or the SCM
    #: could not answer: `already_fixed_proven` then refuses rather than assume.
    branch_pushed: bool | None = None
    #: The commit judged (ADR 0171), its repository and its branch: what the merge will name.
    head_sha: str | None = None
    repository: str | None = None
    branch: str | None = None

    @classmethod
    def of(cls, ctx: GateContext) -> DeliveryFacts:
        """The facts in a gate's context; when the provider failed, what a failure of every read gives."""
        raw = ctx.facts.get(PROVIDER)
        if raw is None:
            return cls(diff_available=False)
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> DeliveryFacts:
        known = {f.name for f in fields(cls)}
        values = {key: value for key, value in raw.items() if key in known}
        if values.get("max_diff") is not None:
            files, lines = values["max_diff"]
            values["max_diff"] = (int(files), int(lines))
        return cls(**values)

    def as_mapping(self) -> dict[str, Any]:
        return asdict(self)


def delivery_context(facts: DeliveryFacts | None = None, **neutral: Any) -> GateContext:
    """A gate context holding these delivery facts, and the core's own fields: what a test of these
    gates builds, and what the gate activity builds from the provider's answer."""
    return GateContext(facts={PROVIDER: (facts or DeliveryFacts()).as_mapping()}, **neutral)


def _without_diff(name: str) -> GateOutcome:
    return GateOutcome(
        name,
        False,
        detail="diff unavailable: this guarantee could not be evaluated (SCM connector)",
    )


def _scope_respected(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The changed files stay out of the paths the policy forbids, and inside the allowed ones.

    Two readings, chosen by the declaration (ADR 0171). Without `over`, the files are a run's — what
    it wrote since the commit where it began, handed over by the evaluation — and the allowed paths are
    `paths`, else that run's. With `over: work_item`, the files are the work item's whole diff at the
    judged commit and nothing of a run is read: the allowed paths are `paths` and only `paths` — a
    work item's own are rewritten by its steps' results, so they are not what a merge is held to —
    and without `paths` the forbidden paths alone are held."""
    d = DeliveryFacts.of(ctx)
    if not d.diff_available:
        return _without_diff("scope_respected")
    over = params.get("over")
    if over is not None and over != OVER_WORK_ITEM:
        return GateOutcome(
            "scope_respected",
            False,
            detail=f"`over: {over}` is not a reading of this guarantee: its only value is `{OVER_WORK_ITEM}`",
        )
    whole = over == OVER_WORK_ITEM
    # A run's writes, when the provider could read them and this declaration is judged on a run.
    changed = (
        d.run_changed_files
        if d.run_changed_files is not None and judged_on_a_run("scope_respected", params)
        else d.changed_files
    )
    denied = [p for p in changed if matches_any(p, d.denied_paths)]
    if denied:
        return GateOutcome(
            "scope_respected",
            False,
            detail=f"{len(denied)} file(s) under a path the project's policy forbids (scope.deny_paths)",
            annotations=denied,
        )
    allowed = params.get("paths") or ([] if whole else d.allowed_paths)
    if not allowed:
        return GateOutcome(
            "scope_respected",
            True,
            detail="no allowed paths declared on this transition: only the paths the policy forbids are held"
            if whole
            else "no allowed paths declared",
        )
    out = [p for p in changed if not matches_any(p, list(allowed))]
    kind = "declared" if whole else "allowed"
    return GateOutcome(
        "scope_respected",
        not out,
        detail=f"within the {kind} paths" if not out else f"{len(out)} file(s) outside the {kind} paths",
        annotations=out,
    )


def _headings(text: str) -> set[str]:
    """The Markdown headings of a text, without hashes or case: `## Decision Outcome` → `decision outcome`."""
    return {
        line.lstrip("#").strip().lower()
        for line in text.splitlines()
        if line.startswith("#") and line.lstrip("#").startswith(" ")
    }


def _markdown_sections(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The documents the branch adds carry their sections: `{paths, sections, patterns,
    min_files}`. An ADR in the MADR 4 format must say its context, its options and its decision, and
    say it IN the file, read in the diff, not in the agent's summary."""
    d = DeliveryFacts.of(ctx)
    if not d.diff_available:
        return _without_diff("markdown_sections")
    paths = [str(c) for c in params.get("paths") or []]
    if not paths:
        return GateOutcome("markdown_sections", False, detail="no path: `paths:` missing on the guarantee")
    files = {path: text for path, text in d.added_text.items() if matches_any(path, paths)}
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


def _docs_updated(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The work item's whole branch updates its documentation when it changes anything else.

    Params: `docs` (globs of documentation files), `ignore` (globs that need no documentation:
    tests, lockfiles, CI), `min_files` (documentation files to update, default 1). A documentation
    file counts when the diff adds text to it, or changes it with no patch (the forge omits big
    patches); a file that is only deleted does not. A changelog line is enough. The gate reads the
    branch diff, not the agent's report: it never takes the writer's word that nothing needed saying.
    """
    d = DeliveryFacts.of(ctx)
    if not d.diff_available:
        return _without_diff("docs_updated")
    docs = [str(p) for p in params.get("docs") or DOCS_PATHS]
    ignore = [str(p) for p in params.get("ignore") or NO_DOCS_NEEDED]
    minimum = int(params.get("min_files", 1))
    needing = [p for p in d.changed_files if not matches_any(p, docs) and not matches_any(p, ignore)]
    if not needing:
        return GateOutcome("docs_updated", True, detail="nothing outside documentation and tests changed")
    updated = sorted(
        p
        for p in d.changed_files
        if matches_any(p, docs) and (p not in d.added_text or d.added_text[p].strip())
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


def _adr_number_free(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The number of the ADR the branch adds is not already taken on the default branch (#287).

    The architect numbers "the largest existing plus one", read at the moment of writing: two studies
    that run together take the same number, and the second would merge a duplicate. Nothing
    reserves a number; this guarantee, set at the merge, refuses the duplicate at the last useful moment.
    A path already present as is on the default branch is not an addition: it is the same
    file (or an add conflict the forge refuses by itself).
    """
    d = DeliveryFacts.of(ctx)
    if not d.diff_available:
        return _without_diff("adr_number_free")
    pattern = str(params.get("pattern") or ADR_PATTERN)
    if d.default_branch_files is None:
        if not any(fnmatch.fnmatch(p, pattern) for p in d.changed_files):
            return GateOutcome("adr_number_free", True, detail="no numbered record added by the change")
        return GateOutcome(
            "adr_number_free",
            False,
            detail="default branch unavailable: the numbers already taken could not be listed "
            "(SCM connector)",
        )
    existing = sorted(p for p in d.default_branch_files if fnmatch.fnmatch(p, pattern))
    added = [p for p in d.changed_files if fnmatch.fnmatch(p, pattern) and p not in existing]
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


def _diff_size_max(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    d = DeliveryFacts.of(ctx)
    if not d.diff_available:
        return _without_diff("diff_size_max")
    if "files" not in params and d.max_diff is not None:
        # The project's policy bounds (`scope.max_diff_*`) when the declaration names no file count.
        params = {**params, "files": d.max_diff[0], "lines": params.get("lines", d.max_diff[1])}
    max_files = int(params.get("files", params.get("n", 60)))
    max_lines = int(params.get("lines", 100_000))
    files = len(d.changed_files)
    lines = d.additions + d.deletions
    if files > max_files:
        return GateOutcome("diff_size_max", False, detail=f"{files} files changed > {max_files}")
    if lines > max_lines:
        return GateOutcome("diff_size_max", False, detail=f"{lines} lines changed > {max_lines}")
    return GateOutcome("diff_size_max", True, detail=f"{files} files / {lines} lines")


def _no_secrets(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    d = DeliveryFacts.of(ctx)
    if not d.diff_available:
        return _without_diff("no_secrets")
    found = d.secrets_found
    return GateOutcome(
        "no_secrets",
        not found,
        detail="no secret detected" if not found else f"secrets detected: {', '.join(found)}",
        annotations=found,
    )


def _coverage_delta_min(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    threshold = float(params.get("x", params.get("min", 0.0)))
    delta = ctx.result.evidence.value("coverage_delta") if ctx.result is not None else None
    if not isinstance(delta, int | float) or isinstance(delta, bool):
        return GateOutcome("coverage_delta_min", False, detail="coverage delta unknown")
    return GateOutcome(
        "coverage_delta_min",
        delta >= threshold,
        detail=f"coverage {delta:+.2f} (threshold {threshold:+.2f})",
    )


def _ci_green(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    d = DeliveryFacts.of(ctx)
    # `pending` is what `checks_conclusion` returns while a check runs: taking it for a
    # failure escalated a work item whose CI, green 13 s later, had not finished (#5 of the
    # dev tenant, 09/10, S22-14).
    if d.ci_status in (None, "pending"):
        return GateOutcome("ci_green", False, pending=True, detail="CI pending")
    ok = d.ci_status in {"success", "succeeded", "neutral"}
    return GateOutcome("ci_green", ok, detail=f"CI {d.ci_status}")


def _review_approved(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    d = DeliveryFacts.of(ctx)
    if d.review_state is None:
        return GateOutcome("review_approved", False, pending=True, detail="review pending")
    ok = d.review_state == "approved"
    return GateOutcome("review_approved", ok, detail=f"review {d.review_state}")


def _scans_ok(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    d = DeliveryFacts.of(ctx)
    required = list(params.get("scanners") or sorted(d.scans) or ["semgrep", "trivy", "gitleaks"])
    if not d.scans:
        if d.ci_status in {"success", "failure", "neutral"}:
            return GateOutcome(
                "scans_ok",
                False,
                detail="no security scan declared on the PR: add semgrep/trivy/gitleaks "
                "to the CI, or remove the `scans_ok` gate from the workflow",
            )
        return GateOutcome("scans_ok", False, pending=True, detail="scans pending")
    failed = [name for name in required if d.scans.get(name, "pending") not in {"ok", "passed", "skipped"}]
    pending = [name for name in required if d.scans.get(name) is None]
    if pending:
        return GateOutcome("scans_ok", False, pending=True, detail=f"scans pending: {', '.join(pending)}")
    return GateOutcome(
        "scans_ok", not failed, detail="scans OK" if not failed else f"failed: {', '.join(failed)}"
    )


# The provenance is read on the PR, like the scans: a check whose name contains `provenance`
# (`attest-build-provenance`, `slsa-provenance`…). Nothing filled in `signed`: the guarantee
# waited forever, and an `advanced` work item never merged (#283).
def _provenance_signed(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    d = DeliveryFacts.of(ctx)
    if d.signed is None:
        if d.ci_status in {"success", "failure", "neutral", "succeeded"}:
            return GateOutcome(
                "provenance_signed",
                False,
                detail="no provenance check on the pull request: add one to the CI (a check named "
                "`provenance`), or remove the `provenance_signed` guarantee from the workflow",
            )
        return GateOutcome("provenance_signed", False, pending=True, detail="provenance pending")
    return GateOutcome("provenance_signed", d.signed, detail="signed" if d.signed else "not signed")


def _already_fixed_proven(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """The ticket's fix is already on the default branch, on measured evidence only (#539).

    The run changed nothing (`unchanged_run`), pushed no branch (read from the SCM), and the tests
    pass on that unchanged checkout — which is the default branch: the runner ran them, at least one,
    none failed (`tests_run` and `tests_failed` in `evidence.measured`). Anything missing refuses,
    and says what is missing."""
    d = DeliveryFacts.of(ctx)
    name = ALREADY_FIXED_GATE
    result = ctx.result
    if result is None:
        return GateOutcome(name, False, detail="no stage result")
    evidence = result.evidence
    if not unchanged_run(result):
        return GateOutcome(name, False, detail="the run did not measure an empty change")
    if d.branch_pushed is None:
        return GateOutcome(name, False, detail="whether a branch was pushed could not be read from the SCM")
    if d.branch_pushed:
        return GateOutcome(name, False, detail="a work branch was pushed")
    if not (evidence.is_measured("tests_run") and evidence.is_measured("tests_failed")):
        return GateOutcome(name, False, detail="the platform did not run the tests (none measured)")
    if not evidence.value("tests_run"):
        return GateOutcome(name, False, detail="no test ran on the default branch")
    if evidence.value("tests_failed") or evidence.value("tests_passed") is False:
        failed = evidence.value("tests_failed") or "some"
        return GateOutcome(name, False, detail=f"{failed} test(s) fail on the default branch")
    return GateOutcome(
        name,
        True,
        detail=(
            f"nothing to change (0 files, 0 lines, no commit, no branch) and {evidence.value('tests_run')} "
            "test(s) pass on the default branch, 0 failed — measured by the platform"
        ),
    )


# ───────────────────────────── what each declaration holds (the process view) ─────────────────────────────


def _listed(values: Any) -> str:
    return ", ".join(f"`{v}`" for v in values)


def _scope_summary(params: Mapping[str, Any]) -> str:
    if params.get("over") == OVER_WORK_ITEM:
        return "the whole change stays out of the paths the policy forbids" + (
            " and within the declared ones" if params.get("paths") else ""
        )
    return "the agent stayed within its allowed paths"


def _scope_detail(params: Mapping[str, Any]) -> str:
    paths = params.get("paths")
    if params.get("over") == OVER_WORK_ITEM:
        return "read on the work item's whole diff, whichever step ran last; " + (
            f"only files under {_listed(paths)}"
            if paths
            else "no allowed paths are held, only the forbidden ones"
        )
    return (
        f"only files under {_listed(paths)}"
        if paths
        else "only the work item's allowed paths; with none declared, anything passes"
    )


def _size_detail(params: Mapping[str, Any]) -> str:
    files = params.get("files", params.get("n"))
    lines = params.get("lines")
    return (
        f"at most {files if files is not None else 60} files and "
        f"{lines if lines is not None else 100_000} lines changed"
        + (" (the platform's defaults)" if files is None and lines is None else "")
    )


def _coverage_detail(params: Mapping[str, Any]) -> str:
    threshold = float(params.get("x", params.get("min", 0.0)))
    return f"coverage may not move by less than {threshold:+.2f} points"


@dataclass(frozen=True, slots=True)
class _Declared:
    fn: GateFn
    summary: str | Describe
    asynchronous: bool = False
    reads: bool = True
    detail: Describe | None = None
    requires: tuple[str, str] | None = None


#: name -> the gate, what it says it checks, and how it is registered.
GATES: dict[str, _Declared] = {
    "scope_respected": _Declared(_scope_respected, _scope_summary, detail=_scope_detail),
    "diff_size_max": _Declared(_diff_size_max, "the change stays under the size limit", detail=_size_detail),
    "no_secrets": _Declared(_no_secrets, "no secret leaks into the change"),
    "markdown_sections": _Declared(
        _markdown_sections,
        "the documents the change adds carry their required sections",
        requires=("paths", "`paths:` as a parameter of the guarantee"),
    ),
    "adr_number_free": _Declared(
        _adr_number_free, "the decision record's number is not already taken on the default branch"
    ),
    "docs_updated": _Declared(
        _docs_updated, "the change updates its documentation when it changes anything else"
    ),
    # The coverage the runner MEASURES in the cloned repository: a repository, not a CI (S21-23). It
    # reads the stage result, not the provider.
    "coverage_delta_min": _Declared(
        _coverage_delta_min,
        "test coverage does not drop below the threshold",
        reads=False,
        detail=_coverage_detail,
    ),
    # `ci_green` and `scans_ok` read the PR's checks THROUGH THE REPOSITORY (`scm.get_pr`, in the
    # provider), GitHub Actions as well as Tekton. They declared a `ci` that nothing read: a project
    # on GitHub Actions was asked for a Tekton it does not have (S21-23).
    "ci_green": _Declared(_ci_green, "the CI pipeline is green", asynchronous=True),
    "review_approved": _Declared(_review_approved, "the review is approved", asynchronous=True),
    "scans_ok": _Declared(_scans_ok, "security scans find nothing blocking", asynchronous=True),
    "provenance_signed": _Declared(_provenance_signed, "the build provenance is signed", asynchronous=True),
    ALREADY_FIXED_GATE: _Declared(
        _already_fixed_proven,
        "the step changed nothing, pushed no branch, and the tests pass on the default branch, as measured",
    ),
}


def register_gates() -> None:
    """Register the twelve gates through the core's gate seam, under their stored names. Idempotent."""
    for name, declared in GATES.items():
        gate(
            name,
            asynchronous=declared.asynchronous,
            needs=("scm",),
            reads=(PROVIDER,) if declared.reads else (),
            plugin=NAME,
            workspace=name in DIFF_GATES,
            run_scoped=name in RUN_SCOPED_GATES,
            summary=declared.summary,
            detail=declared.detail,
            requires=declared.requires,
        )(declared.fn)


__all__ = [
    "ADR_PATTERN",
    "DIFF_GATES",
    "GATES",
    "PROVIDER",
    "PULL_REQUEST_GATES",
    "RUN_SCOPED_GATES",
    "DeliveryFacts",
    "delivery_context",
    "register_gates",
]

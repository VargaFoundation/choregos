# SPDX-License-Identifier: Apache-2.0
"""Gate evaluation: the mechanism that verifies what the agent claims."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from choregos_adapters.errors import UpstreamError
from choregos_api.db.models import CostLedger, Run
from choregos_contracts import StageResult
from choregos_core import GateContext, GateOutcome, evaluate, is_async_gate, matches_any, scan_secrets
from choregos_core.domain import DiffSummary, PrRef
from choregos_core.gates import (
    ADR_PATTERN,
    ALREADY_FIXED_GATE,
    DIFF_GATES,
    judged_on_a_run,
    unchanged_run,
)
from choregos_core.run_journal import GATE_OUTCOME
from temporalio import activity

from .base import db, load_work_item, project_bundle, scm_for
from .judged import record_judged
from .repositories import repo_slug as _repo_slug
from .repositories import target_or_none


@activity.defn(name="evaluate_gates")
async def evaluate_gates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate the gates of a transition against the real diff and the external state.

    The synchronous gates decide at once; the asynchronous ones return `pending` until the
    awaited event (CI, review) has arrived.
    """
    gates: list[dict[str, Any]] = payload.get("gates", [])
    if not gates:
        return []
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        run = await session.get(Run, payload["run_id"]) if payload.get("run_id") else None
        result = StageResult.model_validate(run.result) if run and run.result else None

        allowed = list(run.allowed_paths if run else item.allowed_paths or [])
        diff = None
        changed: list[str] = []
        additions = deletions = 0
        secrets: list[str] = []
        added: dict[str, str] = {}
        needs_diff = any(g["name"] in DIFF_GATES for g in gates)
        # Without a repository there is no diff, and no error either: the guarantee refuses by
        # itself (`diff_available`), which is exactly what we want it to do. The repository is the
        # work item's target (ADR 0124); one the project does not serve has no diff either.
        found = target_or_none(bundle, item)
        repo_config = found.repo if found is not None else None
        scm = (
            await scm_for(session, bundle, repo_config.url)
            if repo_config is not None
            else bundle.adapters.scm
        )
        branch = (result.artifacts.branch if result else None) or (
            bundle.config.branch_for(item.tracker_key, repo=repo_config) if repo_config is not None else ""
        )
        # The commit this evaluation judges (ADR 0171): the head of the branch, read once. Every read
        # of the branch below is a read of THAT commit, never of what the name designates by then.
        head, head_unread = await _head(scm, repo_config, branch)
        if needs_diff and repo_config is not None:
            repo = _repo_slug(repo_config.url)
            try:
                if head is None:
                    # No commit to compare: the branch is not there — what a comparison by name
                    # answered with a 404 — or its head could not be read.
                    raise head_unread or UpstreamError("scm", f"no branch `{branch}`", status_code=404)
                diff = await scm.compare(repo, repo_config.default_branch, head)
            except Exception as exc:
                # A run that changed nothing pushed no branch: its diff is EMPTY, not unreadable
                # (#536). Any other failure stays "diff unavailable" and the guarantee refuses.
                diff = empty_diff_of_unchanged_run(
                    exc, result, repo_config.default_branch, branch, cloned=_run_cloned(run)
                )
            if diff is not None:
                changed = diff.paths()
                additions, deletions = diff.additions, diff.deletions
                for file in diff.files:
                    if file.patch:
                        secrets.extend(scan_secrets(file.patch))
                        added[file.path] = added_text_of(file.patch)

        branch_pushed = await _branch_pushed(scm, repo_config, branch) if _reads_branch(gates) else None

        ci_status = None
        review_state = None
        scans: dict[str, str] = {}
        signed: bool | None = None
        if any(is_async_gate(g["name"]) for g in gates) and item.pr_url and repo_config is not None:
            repo = _repo_slug(repo_config.url)
            number = int(item.pr_url.rsplit("/", 1)[-1]) if item.pr_url.rsplit("/", 1)[-1].isdigit() else 0
            if number:
                try:
                    pr = await scm.get_pr(PrRef(repo=repo, number=number))
                    # The checks, the review and the scans count when they are those of the judged
                    # commit. A pull request whose head is elsewhere says nothing of it: the
                    # guarantees stay pending and are read again.
                    if head is not None and pr.head_sha == head:
                        ci_status = pr.checks_conclusion()
                        review_state = pr.review_conclusion()
                        scans = {
                            check.name: ("ok" if check.conclusion == "success" else "failed")
                            for check in pr.checks
                            if check.name in {"semgrep", "trivy", "gitleaks"}
                        }
                        signed = _provenance(pr.checks)
                except Exception:
                    ci_status = None

        # What the run really called in the catalogue: the registry, not the result. A place held under
        # the tool-call cap is not a call (S57-03): one whose request died never answered.
        calls: list[str] = []
        if run is not None:
            from choregos_core.tool_ledger import IN_FLIGHT
            from sqlalchemy import select

            rows = await session.execute(
                select(CostLedger.model).where(
                    CostLedger.run_id == run.id, CostLedger.kind == "tool", CostLedger.provider != IN_FLIGHT
                )
            )
            calls = [str(m) for m in rows.scalars() if m]
        # The scope is judged on what the RUN wrote, since the commit where it began (S22-11): from
        # the base, it counted the work of the earlier steps. On the dev tenant, on 10/09, the ADR of
        # study #5 turned red on the Makefile that the framing step had pushed. A declaration that
        # says `over: work_item` reads the whole diff instead, and no run (ADR 0171).
        run_scope = await _run_diff(scm, repo_config, result, head, gates) if diff is not None else None
        context = GateContext(
            result=result,
            tool_calls=calls,
            changed_files=changed,
            additions=additions,
            deletions=deletions,
            allowed_paths=allowed,
            denied_paths=bundle.engine.deny_paths(),
            secrets_found=sorted(set(secrets)),
            ci_status=ci_status,
            review_state=review_state,
            scans=scans,
            signed=signed,
            flags=list(payload.get("flags", [])),
            expected_outputs=list(payload.get("expected_outputs", [])),
            # `needs_diff` says a guarantee depends on it; `diff` says whether we got it.
            diff_available=(diff is not None) if needs_diff else True,
            required_flag=payload.get("required_flag"),
            added_text=added,
            documents=dict(item.documents or {}),
            fields=dict(item.fields or {}),
            parent_documents=await _parent_documents(session, item),
            branch_pushed=branch_pushed,
        )
        outcomes = [
            await _judge(gate, context, bundle, (scm, repo_config), diff is not None, run_scope)
            for gate in gates
        ]

        await _publish_check_runs(scm, repo_config, item, outcomes, head)
        if run is not None:
            await _journal(session, run, outcomes, head)
        if head is not None and repo_config is not None:
            # What the merge will name: the commit these verdicts are about, and no other.
            record_judged(item, repository=_repo_slug(repo_config.url), branch=branch, sha=head)
        return [
            {
                "name": o.name,
                "passed": o.passed,
                "pending": o.pending,
                "detail": o.detail,
                "annotations": list(o.annotations),
            }
            for o in outcomes
        ]


async def _parent_documents(session: Any, item: Any) -> dict[str, Any] | None:
    """The documents of the work item that opened this one by a fan-out, read from the parent's own
    row; `None` for a work item nobody spawned. `documents.parent` is written by the platform
    only (the API never accepts documents from a person), so a hand-made child has none (S38-17)."""
    from .children import PARENT

    link = (item.documents or {}).get(PARENT)
    if not isinstance(link, dict) or not link.get("work_item_id"):
        return None
    parent = await load_work_item(session, link["work_item_id"])
    if parent.project_id != item.project_id:
        return None
    return dict(parent.documents or {})


def _run_cloned(run: Run | None) -> bool:
    """Whether the run cloned the repository: `False` only for a recorded `repo: null` (ADR 0119)."""
    stage_input = (run.stage_input if run is not None else None) or {}
    return "repo" not in stage_input or stage_input["repo"] is not None


def empty_diff_of_unchanged_run(
    exc: Exception, result: StageResult | None, base: str, branch: str, *, cloned: bool = True
) -> DiffSummary | None:
    """The empty diff of a run that changed nothing, or `None` when the diff is truly unreadable.

    On the dev tenant (#536), an implement run found the bug already fixed, changed nothing, and
    pushed no branch: the SCM answered 404 on the comparison and the three diff guarantees refused
    with "diff unavailable", three attempts in a row, then `needs_human`. Both conditions are
    required: the SCM says the branch does not exist (not an outage, a timeout or a 403), AND the
    runner MEASURED an empty change (git's `diff_files` / `diff_lines`, not the agent's word) with
    no commit. A run whose push failed has commits and a non-empty diff: it is still refused.
    """
    if not (isinstance(exc, UpstreamError) and exc.status_code == 404):
        return None
    # A run that never cloned the repository (`workspace: none`, ADR 0119) cannot have pushed to
    # it: what it wrote in its empty directory is no change to the repository.
    if not cloned:
        return DiffSummary(base=base, head=branch)
    # A runner that does not measure its diff proves nothing; a measured change or a commit is not empty.
    if not unchanged_run(result):
        return None
    return DiffSummary(base=base, head=branch)


async def _head(scm: Any, repo_config: Any, branch: str) -> tuple[str | None, Exception | None]:
    """The commit the head of `branch` is at, read once per evaluation (ADR 0171).

    `(sha, None)`; `(None, None)` when there is no such branch, or no repository; `(None, error)`
    when the SCM cannot say — a connector that cannot read a head, a 403, an outage. Without a
    commit nothing is judged on the branch: the diff is unavailable, and no merge is named."""
    if repo_config is None or not branch:
        return None, None
    head_of = getattr(scm, "head_of", None)
    if head_of is None:
        return None, UpstreamError("scm", f"the `{type(scm).__name__}` SCM cannot read the head of a branch")
    try:
        return await head_of(_repo_slug(repo_config.url), branch), None
    except Exception as exc:
        return None, exc


def _reads_branch(gates: list[dict[str, Any]]) -> bool:
    return any(g["name"] == ALREADY_FIXED_GATE for g in gates)


async def _branch_pushed(scm: Any, repo_config: Any, branch: str) -> bool | None:
    """Whether the work item's branch exists on its target repository (S22-47, ADR 0124), for
    `already_fixed_proven`.

    `None` — the guarantee refuses — without a repository, or when the SCM cannot answer: a 403 or
    an outage proves nothing either way."""
    if repo_config is None:
        return None
    try:
        return bool(await scm.branch_exists(_repo_slug(repo_config.url), branch))
    except Exception:
        return None


async def _run_diff(
    scm: Any, repo_config: Any, result: StageResult | None, head: str | None, gates: list[dict[str, Any]]
) -> list[str] | None:
    """The files the run changed, from the commit where it began to the judged one (`head`); `None`
    when the run does not say (a runner older than S22-11), no commit is judged, or the comparison
    fails: the work item's diff then holds. Read only for a guarantee judged on a run: one declared
    `over: work_item` reads the work item's whole diff, and nothing of a run (ADR 0171)."""
    start = (result.artifacts.reports.get("start_commit") if result else None) or ""
    on_a_run = any(judged_on_a_run(g["name"], g.get("params")) for g in gates)
    if not start or not on_a_run or repo_config is None:
        return None
    if head is None:  # no commit judged: nothing to measure the run against
        return None
    try:
        diff = await scm.compare(_repo_slug(repo_config.url), start, head)
    except Exception:
        return None
    return list(diff.paths())


async def _judge(
    gate: dict[str, Any],
    context: GateContext,
    bundle: Any,
    target: tuple[Any, Any],
    diff_obtained: bool,
    run_scope: list[str] | None,
) -> GateOutcome:
    """One guarantee, with what it alone reads beyond the common context."""
    params = dict(gate.get("params", {}))
    if gate["name"] == "diff_size_max" and "files" not in params:
        max_files, max_lines = bundle.engine.max_diff()
        params.setdefault("files", max_files)
        params.setdefault("lines", max_lines)
    if gate["name"] == "adr_number_free" and diff_obtained:
        already_there = await _default_branch_files(*target, params)
        return evaluate(gate["name"], replace(context, default_branch_files=already_there), params)
    if run_scope is not None and judged_on_a_run(gate["name"], params):
        return evaluate(gate["name"], replace(context, changed_files=run_scope), params)
    return evaluate(gate["name"], context, params)


async def _default_branch_files(scm: Any, repo_config: Any, params: dict[str, Any]) -> list[str] | None:
    """The files already on the target's default branch, in the folder of the pattern of
    `adr_number_free` (`docs/adr/[0-9]...` gives `docs/adr`); `None` when the connector cannot list or
    fails: the guarantee then refuses rather than declare free a number it could not compare."""
    list_files = getattr(scm, "list_files", None)
    if repo_config is None or list_files is None:
        return None
    pattern = str(params.get("pattern") or ADR_PATTERN)
    fixed = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
    folder = fixed.rsplit("/", 1)[0] if "/" in fixed else ""
    try:
        return list(await list_files(_repo_slug(repo_config.url), repo_config.default_branch, folder))
    except Exception:
        return None


def added_text_of(patch: str) -> str:
    """The lines a unified patch ADDS, without their `+`: a new file, entire."""
    return "\n".join(
        line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")
    )


async def _journal(session: Any, run: Run, outcomes: list[GateOutcome], head: str | None = None) -> None:
    """File each verdict in the run's journal (`gate.outcome`), with the commit it was given on
    (`head_sha`, ADR 0171) when the evaluation judged one.

    The verdicts were only returned to the workflow, which decided and then forgot them: no screen
    could say WHICH guarantees had run nor what they had answered, and the product's central promise
    was invisible (state of play of 2026-09-24). The run's journal is what the page already shows;
    the verdicts take their place there.

    Numbered after the highest line, at 1 at least (S65-28, finding #1608). The verdicts are judged
    once the run's process has finished, and the runner posts its last line (`run.result`) before it
    posts its result: the numbers after the highest are no longer the runner's to take. A journal that
    holds only the platform's lines from before the run (`memory.skipped`, numbered below 0) does not
    pull a verdict down to 0, a number a run may still send.
    """
    from choregos_api.db.models import RunEvent
    from choregos_core import utcnow
    from sqlalchemy import func, select

    last = (await session.execute(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run.id))).scalar()
    seq = max(int(last or 0), 0)
    for outcome in outcomes:
        seq += 1
        session.add(
            RunEvent(
                run_id=run.id,
                seq=seq,
                type=GATE_OUTCOME,
                payload={
                    "name": outcome.name,
                    "passed": outcome.passed,
                    "pending": outcome.pending,
                    "detail": outcome.detail,
                    "annotations": list(outcome.annotations),
                    **({"head_sha": head} if head else {}),
                },
                ts=utcnow(),
            )
        )


async def _publish_check_runs(
    scm: Any, repo_config: Any, item: Any, outcomes: list[GateOutcome], head: str | None
) -> None:
    """Check-runs `choregos/scope` and `choregos/evidence` (S3-05): the verdict is visible in the PR,
    on the work item's target repository (ADR 0124), on the commit it was given on (`head`, ADR
    0171) — never on a commit a run declared. Without a judged commit there is nothing to mark."""
    if not item.pr_url or repo_config is None or head is None or not hasattr(scm, "create_check_run"):
        return
    repo = _repo_slug(repo_config.url)
    mapping = {"scope_respected": "choregos/scope", "evidence_present": "choregos/evidence"}
    for outcome in outcomes:
        name = mapping.get(outcome.name)
        if name is None:
            continue
        await scm.create_check_run(
            repo,
            head,
            name,
            "success" if outcome.passed else "failure",
            outcome.detail,
            list(outcome.annotations),
        )


@activity.defn(name="check_scope_violations")
async def check_scope_violations(payload: dict[str, Any]) -> list[str]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        allowed = list(item.allowed_paths or [])
        denied = bundle.engine.deny_paths()
        found = target_or_none(bundle, item)
        repo_config = found.repo if found is not None else None
        if not (allowed or denied) or repo_config is None:
            # Without a repository there are no files to compare: no violation to report. That is
            # an absence of material, not an absence of violation, and the `scope_respected`
            # guarantee refuses (`diff_available`).
            return []
        repo = _repo_slug(repo_config.url)
        branch = bundle.config.branch_for(item.tracker_key, repo=repo_config)
        scm = await scm_for(session, bundle, repo_config.url)
        # The commit at the head of the branch, read once (ADR 0171); a branch that is not there is
        # compared by its name, and the SCM answers as it did.
        head, unread = await _head(scm, repo_config, branch)
        if unread is not None:
            raise unread
        diff = await scm.compare(repo, repo_config.default_branch, head or branch)
        return [
            path
            for path in diff.paths()
            if matches_any(path, denied) or (allowed and not matches_any(path, allowed))
        ]


def _provenance(checks: list[Any]) -> bool | None:
    """A finished provenance check on the PR (its name contains `provenance`): true if it succeeded.
    None, or not finished yet: we do not know, and the guarantee will say so when the CI is done."""
    finished = [c for c in checks if "provenance" in c.name.lower() and c.conclusion]
    if not finished:
        return None
    return all(c.conclusion == "success" for c in finished)

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
from choregos_core.gates import ADR_PATTERN, ALREADY_FIXED_GATE, DIFF_GATES, unchanged_run
from temporalio import activity

from .base import db, load_work_item, project_bundle


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
        # itself (`diff_available`), which is exactly what we want it to do.
        repo_config = bundle.config.repo
        if needs_diff and repo_config is not None:
            branch = (result.artifacts.branch if result else None) or bundle.config.branch_for(
                item.tracker_key
            )
            repo = _repo_slug(repo_config.url)
            try:
                diff = await bundle.adapters.scm.compare(repo, repo_config.default_branch, branch)
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

        branch_pushed = await _branch_pushed(bundle, item, result) if _reads_branch(gates) else None

        ci_status = None
        review_state = None
        scans: dict[str, str] = {}
        signed: bool | None = None
        if any(is_async_gate(g["name"]) for g in gates) and item.pr_url and repo_config is not None:
            repo = _repo_slug(repo_config.url)
            number = int(item.pr_url.rsplit("/", 1)[-1]) if item.pr_url.rsplit("/", 1)[-1].isdigit() else 0
            if number:
                try:
                    pr = await bundle.adapters.scm.get_pr(PrRef(repo=repo, number=number))
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
        # study #5 turned red on the Makefile that the framing step had pushed.
        run_scope = await _run_diff(bundle, result, branch, gates) if diff is not None else None
        context = GateContext(
            result=result,
            tool_calls=calls,
            changed_files=changed,
            additions=additions,
            deletions=deletions,
            allowed_paths=allowed,
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
        outcomes = [await _judge(gate, context, bundle, diff is not None, run_scope) for gate in gates]

        await _publish_check_runs(bundle, item, outcomes, result)
        if run is not None:
            await _journal(session, run, outcomes)
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


def _reads_branch(gates: list[dict[str, Any]]) -> bool:
    return any(g["name"] == ALREADY_FIXED_GATE for g in gates)


async def _branch_pushed(bundle: Any, item: Any, result: StageResult | None) -> bool | None:
    """Whether the work item's branch exists on the SCM (S22-47), for `already_fixed_proven`.

    `None` — the guarantee refuses — without a repository, or when the SCM cannot answer: a 403 or
    an outage proves nothing either way."""
    repo_config = bundle.config.repo
    if repo_config is None:
        return None
    branch = (result.artifacts.branch if result else None) or bundle.config.branch_for(item.tracker_key)
    try:
        return bool(await bundle.adapters.scm.branch_exists(_repo_slug(repo_config.url), branch))
    except Exception:
        return None


async def _run_diff(
    bundle: Any, result: StageResult | None, branch: str, gates: list[dict[str, Any]]
) -> list[str] | None:
    """The files the run changed, since the commit where it began; `None` when the run does not
    say (a runner older than S22-11) or the comparison fails: the work item's diff then holds."""
    start = (result.artifacts.reports.get("start_commit") if result else None) or ""
    if not start or not any(g["name"] == "scope_respected" for g in gates) or bundle.config.repo is None:
        return None
    try:
        diff = await bundle.adapters.scm.compare(_repo_slug(bundle.config.repo.url), start, branch)
    except Exception:
        return None
    return list(diff.paths())


async def _judge(
    gate: dict[str, Any],
    context: GateContext,
    bundle: Any,
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
        already_there = await _default_branch_files(bundle, params)
        return evaluate(gate["name"], replace(context, default_branch_files=already_there), params)
    if gate["name"] == "scope_respected" and run_scope is not None:
        return evaluate(gate["name"], replace(context, changed_files=run_scope), params)
    return evaluate(gate["name"], context, params)


async def _default_branch_files(bundle: Any, params: dict[str, Any]) -> list[str] | None:
    """The files already on the default branch, in the folder of the pattern of `adr_number_free`
    (`docs/adr/[0-9]...` gives `docs/adr`); `None` when the connector cannot list or fails: the
    guarantee then refuses rather than declare free a number it could not compare."""
    repo_config = bundle.config.repo
    list_files = getattr(bundle.adapters.scm, "list_files", None)
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


async def _journal(session: Any, run: Run, outcomes: list[GateOutcome]) -> None:
    """File each verdict in the run's journal (`gate.outcome`).

    The verdicts were only returned to the workflow, which decided and then forgot them: no screen
    could say WHICH guarantees had run nor what they had answered, and the product's central promise
    was invisible (state of play of 2026-09-24). The run's journal is what the page already shows;
    the verdicts take their place there.
    """
    from choregos_api.db.models import RunEvent
    from choregos_core import utcnow
    from sqlalchemy import func, select

    last = (await session.execute(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run.id))).scalar()
    seq = int(last or 0)
    for outcome in outcomes:
        seq += 1
        session.add(
            RunEvent(
                run_id=run.id,
                seq=seq,
                type="gate.outcome",
                payload={
                    "name": outcome.name,
                    "passed": outcome.passed,
                    "pending": outcome.pending,
                    "detail": outcome.detail,
                    "annotations": list(outcome.annotations),
                },
                ts=utcnow(),
            )
        )


async def _publish_check_runs(bundle: Any, item: Any, outcomes: list[GateOutcome], result: Any) -> None:
    """Check-runs `choregos/scope` and `choregos/evidence` (S3-05): the verdict is visible in the PR."""
    if not item.pr_url or not hasattr(bundle.adapters.scm, "create_check_run"):
        return
    repo = _repo_slug(bundle.config.repo.url)
    sha = (result.artifacts.commits[-1] if result and result.artifacts.commits else None) or "HEAD"
    mapping = {"scope_respected": "choregos/scope", "evidence_present": "choregos/evidence"}
    for outcome in outcomes:
        name = mapping.get(outcome.name)
        if name is None:
            continue
        await bundle.adapters.scm.create_check_run(
            repo,
            sha,
            name,
            "success" if outcome.passed else "failure",
            outcome.detail,
            list(outcome.annotations),
        )


def _repo_slug(url: str) -> str:
    """`https://github.com/acme/billing.git` → `acme/billing`."""
    trimmed = url.removesuffix(".git").rstrip("/")
    parts = trimmed.split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else trimmed


@activity.defn(name="check_scope_violations")
async def check_scope_violations(payload: dict[str, Any]) -> list[str]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        allowed = list(item.allowed_paths or [])
        repo_config = bundle.config.repo
        if not allowed or repo_config is None:
            # Without a repository there are no files to compare: no violation to report. That is
            # an absence of material, not an absence of violation, and the `scope_respected`
            # guarantee refuses (`diff_available`).
            return []
        repo = _repo_slug(repo_config.url)
        branch = bundle.config.branch_for(item.tracker_key)
        diff = await bundle.adapters.scm.compare(repo, repo_config.default_branch, branch)
        return [path for path in diff.paths() if not matches_any(path, allowed)]


def _provenance(checks: list[Any]) -> bool | None:
    """A finished provenance check on the PR (its name contains `provenance`): true if it succeeded.
    None, or not finished yet: we do not know, and the guarantee will say so when the CI is done."""
    finished = [c for c in checks if "provenance" in c.name.lower() and c.conclusion]
    if not finished:
        return None
    return all(c.conclusion == "success" for c in finished)

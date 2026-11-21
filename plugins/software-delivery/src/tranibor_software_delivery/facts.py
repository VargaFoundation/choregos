# SPDX-License-Identifier: Apache-2.0
"""The `delivery` context provider and its `judged` hook (ADR 0181, round C2): what the twelve gates of
software delivery read, and what the plugin does once they ruled.

They were the gate activity's own code (`tranibor_orchestrator.activities.gates.evaluate_gates`) until
round C2. The activity now calls the provider when a declared gate reads it, and the hook after every
evaluation, through the core's gate seam (`tranibor_core.gates.context_provider`). The activity's name
and its payload did not change, so no recorded history moves.

The provider reads the head of the work item's branch ONCE (ADR 0171) and every read of the branch is a
read of that commit: the diff from the default branch, the run's own writes since the commit it began
on, the default branch's files for `adr_number_free`, the pull request's checks and review when its
head is that commit, and whether the branch exists. The hook marks the pull request's checks with the
verdicts (`choregos/scope`, `choregos/evidence`), leaves the judged commit on the work item for the
merge to name, and adds `head_sha` to each verdict's line of the run's journal.

The worker's modules are imported when the provider is called, not when the extension loads: a process
that only validates workflows (the API's publication, the CLI) registers the gates without the worker.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from tranibor_core.gates import (
    ALREADY_FIXED_GATE,
    JudgedRequest,
    ProviderRequest,
    judged_on_a_run,
    scan_secrets,
    unchanged_run,
)

from .gates import ADR_PATTERN, DIFF_GATES, PULL_REQUEST_GATES, DeliveryFacts

#: The check-runs the verdicts are published as on the pull request (S3-05).
CHECK_RUNS = {"scope_respected": "choregos/scope", "evidence_present": "choregos/evidence"}


async def read_delivery_facts(request: ProviderRequest) -> Mapping[str, Any]:
    """The `delivery` facts of one evaluation, for the declarations in `request.gates`."""
    from tranibor_adapters.errors import UpstreamError
    from tranibor_api.db.models import Run
    from tranibor_orchestrator.activities.base import load_work_item, scm_for
    from tranibor_orchestrator.activities.repositories import repo_slug, target_or_none

    session, bundle, result = request.session, request.project, request.result
    item = await load_work_item(session, request.work_item_id)
    run = await session.get(Run, request.run_id) if request.run_id else None
    gates: list[dict[str, Any]] = [{"name": name, "params": dict(params)} for name, params in request.gates]

    allowed = list(run.allowed_paths if run else item.allowed_paths or [])
    diff = None
    changed: list[str] = []
    additions = deletions = 0
    secrets: list[str] = []
    added: dict[str, str] = {}
    needs_diff = any(g["name"] in DIFF_GATES for g in gates)
    # Without a repository there is no diff, and no error either: the guarantee refuses by itself
    # (`diff_available`), which is exactly what we want it to do. The repository is the work item's
    # target (ADR 0124); one the project does not serve has no diff either.
    found = target_or_none(bundle, item)
    repo_config = found.repo if found is not None else None
    scm = await scm_for(session, bundle, repo_config.url) if repo_config is not None else bundle.adapters.scm
    branch = (result.artifacts.branch if result else None) or (
        bundle.config.branch_for(item.tracker_key, repo=repo_config) if repo_config is not None else ""
    )
    # The commit this evaluation judges (ADR 0171): the head of the branch, read once. Every read of
    # the branch below is a read of THAT commit, never of what the name designates by then.
    head, head_unread = await head_of(scm, repo_config, branch)
    if needs_diff and repo_config is not None:
        repo = repo_slug(repo_config.url)
        try:
            if head is None:
                # No commit to compare: the branch is not there — what a comparison by name answered
                # with a 404 — or its head could not be read.
                raise head_unread or UpstreamError("scm", f"no branch `{branch}`", status_code=404)
            diff = await scm.compare(repo, repo_config.default_branch, head)
        except Exception as exc:
            # A run that changed nothing pushed no branch: its diff is EMPTY, not unreadable (#536).
            # Any other failure stays "diff unavailable" and the guarantee refuses.
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

    reads_branch = any(g["name"] == ALREADY_FIXED_GATE for g in gates)
    branch_pushed = await _branch_pushed(scm, repo_config, branch) if reads_branch else None

    ci_status = review_state = None
    scans: dict[str, str] = {}
    signed: bool | None = None
    if any(g["name"] in PULL_REQUEST_GATES for g in gates) and item.pr_url and repo_config is not None:
        ci_status, review_state, scans, signed = await _pull_request_state(
            scm, repo_slug(repo_config.url), item.pr_url, head
        )

    # The scope is judged on what the RUN wrote, since the commit where it began (S22-11): from the
    # base, it counted the work of the earlier steps. On the dev tenant, on 10/09, the ADR of study #5
    # turned red on the Makefile that the framing step had pushed. A declaration that says `over:
    # work_item` reads the whole diff instead, and no run (ADR 0171).
    run_scope = await _run_diff(scm, repo_config, result, head, gates) if diff is not None else None
    default_branch_files: list[str] | None = None
    patterns = sorted(
        {str(g["params"].get("pattern") or ADR_PATTERN) for g in gates if g["name"] == "adr_number_free"}
    )
    if diff is not None and patterns:
        # The files of each declaration's folder, together: each declaration keeps those its own
        # pattern matches. One folder unread, and none is said to be listed.
        listed = [await _default_branch_files(scm, repo_config, pattern) for pattern in patterns]
        if all(files is not None for files in listed):
            default_branch_files = sorted({path for files in listed for path in files or []})

    max_files, max_lines = bundle.engine.max_diff()
    return DeliveryFacts(
        # `needs_diff` says a guarantee depends on it; `diff` says whether we got it.
        diff_available=(diff is not None) if needs_diff else True,
        changed_files=changed,
        additions=additions,
        deletions=deletions,
        run_changed_files=run_scope,
        allowed_paths=allowed,
        denied_paths=bundle.engine.deny_paths(),
        secrets_found=sorted(set(secrets)),
        added_text=added,
        default_branch_files=default_branch_files,
        max_diff=(int(max_files), int(max_lines)),
        ci_status=ci_status,
        review_state=review_state,
        scans=scans,
        signed=signed,
        branch_pushed=branch_pushed,
        head_sha=head,
        repository=repo_slug(repo_config.url) if repo_config is not None else None,
        branch=branch or None,
    ).as_mapping()


async def judged(judgment: JudgedRequest) -> Mapping[str, Any] | None:
    """After every evaluation: the verdicts marked on the pull request, on the commit they were given on;
    that commit left on the work item, for the merge to name (ADR 0171); `head_sha` for the journal.

    The commit is the one the provider read when a gate of the plugin was declared, else the head read
    here, once: a merge transition whose gates are all the core's still names what was judged."""
    from tranibor_orchestrator.activities.base import load_work_item, scm_for
    from tranibor_orchestrator.activities.judged import record_judged
    from tranibor_orchestrator.activities.repositories import repo_slug, target_or_none

    request = judgment.request
    session, bundle = request.session, request.project
    item = await load_work_item(session, request.work_item_id)
    found = target_or_none(bundle, item)
    if found is None:
        return None
    repo_config = found.repo
    scm = await scm_for(session, bundle, repo_config.url)
    if judgment.facts is not None:
        facts = DeliveryFacts.from_mapping(judgment.facts)
        head, branch = facts.head_sha, facts.branch or ""
    else:
        result = request.result
        branch = (result.artifacts.branch if result else None) or bundle.config.branch_for(
            item.tracker_key, repo=repo_config
        )
        head, _ = await head_of(scm, repo_config, branch)
    if head is None:
        return None
    repo = repo_slug(repo_config.url)
    await _publish_check_runs(scm, repo, item, judgment.outcomes, head)
    # What the merge will name: the commit these verdicts are about, and no other.
    record_judged(item, repository=repo, branch=branch, sha=head)
    return {"head_sha": head}


async def _pull_request_state(
    scm: Any, repo: str, pr_url: str, head: str | None
) -> tuple[str | None, str | None, dict[str, str], bool | None]:
    """The checks, the review, the scans and the provenance of the pull request, when its head is the
    judged commit. A pull request whose head is elsewhere says nothing of it: the guarantees stay
    pending and are read again."""
    from tranibor_core.domain import PrRef

    last = pr_url.rsplit("/", 1)[-1]
    number = int(last) if last.isdigit() else 0
    if not number:
        return None, None, {}, None
    try:
        pr = await scm.get_pr(PrRef(repo=repo, number=number))
    except Exception:
        return None, None, {}, None
    if head is None or pr.head_sha != head:
        return None, None, {}, None
    scans = {
        check.name: ("ok" if check.conclusion == "success" else "failed")
        for check in pr.checks
        if check.name in {"semgrep", "trivy", "gitleaks"}
    }
    return pr.checks_conclusion(), pr.review_conclusion(), scans, provenance(pr.checks)


def _run_cloned(run: Any) -> bool:
    """Whether the run cloned the repository: `False` only for a recorded `repo: null` (ADR 0119)."""
    stage_input = (run.stage_input if run is not None else None) or {}
    return "repo" not in stage_input or stage_input["repo"] is not None


def empty_diff_of_unchanged_run(
    exc: Exception, result: Any, base: str, branch: str, *, cloned: bool = True
) -> Any:
    """The empty diff of a run that changed nothing, or `None` when the diff is truly unreadable.

    On the dev tenant (#536), an implement run found the bug already fixed, changed nothing, and
    pushed no branch: the SCM answered 404 on the comparison and the three diff guarantees refused
    with "diff unavailable", three attempts in a row, then `needs_human`. Both conditions are
    required: the SCM says the branch does not exist (not an outage, a timeout or a 403), AND the
    runner MEASURED an empty change (git's `diff_files` / `diff_lines`, not the agent's word) with
    no commit. A run whose push failed has commits and a non-empty diff: it is still refused.
    """
    from tranibor_adapters.errors import UpstreamError
    from tranibor_core.domain import DiffSummary

    if not (isinstance(exc, UpstreamError) and exc.status_code == 404):
        return None
    # A run that never cloned the repository (`workspace: none`, ADR 0119) cannot have pushed to it:
    # what it wrote in its empty directory is no change to the repository.
    if not cloned:
        return DiffSummary(base=base, head=branch)
    # A runner that does not measure its diff proves nothing; a measured change or a commit is not empty.
    if not unchanged_run(result):
        return None
    return DiffSummary(base=base, head=branch)


async def head_of(scm: Any, repo_config: Any, branch: str) -> tuple[str | None, Exception | None]:
    """The commit the head of `branch` is at, read once per evaluation (ADR 0171).

    `(sha, None)`; `(None, None)` when there is no such branch, or no repository; `(None, error)`
    when the SCM cannot say — a connector that cannot read a head, a 403, an outage. Without a
    commit nothing is judged on the branch: the diff is unavailable, and no merge is named."""
    from tranibor_adapters.errors import UpstreamError
    from tranibor_orchestrator.activities.repositories import repo_slug

    if repo_config is None or not branch:
        return None, None
    read = getattr(scm, "head_of", None)
    if read is None:
        return None, UpstreamError("scm", f"the `{type(scm).__name__}` SCM cannot read the head of a branch")
    try:
        return await read(repo_slug(repo_config.url), branch), None
    except Exception as exc:
        return None, exc


async def _branch_pushed(scm: Any, repo_config: Any, branch: str) -> bool | None:
    """Whether the work item's branch exists on its target repository (S22-47, ADR 0124), for
    `already_fixed_proven`.

    `None` — the guarantee refuses — without a repository, or when the SCM cannot answer: a 403 or
    an outage proves nothing either way."""
    from tranibor_orchestrator.activities.repositories import repo_slug

    if repo_config is None:
        return None
    try:
        return bool(await scm.branch_exists(repo_slug(repo_config.url), branch))
    except Exception:
        return None


async def _run_diff(
    scm: Any, repo_config: Any, result: Any, head: str | None, gates: list[dict[str, Any]]
) -> list[str] | None:
    """The files the run changed, from the commit where it began to the judged one (`head`); `None`
    when the run does not say (a runner older than S22-11), no commit is judged, or the comparison
    fails: the work item's diff then holds. Read only for a guarantee judged on a run: one declared
    `over: work_item` reads the work item's whole diff, and nothing of a run (ADR 0171)."""
    from tranibor_orchestrator.activities.repositories import repo_slug

    start = (result.artifacts.reports.get("start_commit") if result else None) or ""
    on_a_run = any(judged_on_a_run(g["name"], g.get("params")) for g in gates)
    if not start or not on_a_run or repo_config is None:
        return None
    if head is None:  # no commit judged: nothing to measure the run against
        return None
    try:
        diff = await scm.compare(repo_slug(repo_config.url), start, head)
    except Exception:
        return None
    return list(diff.paths())


async def _default_branch_files(scm: Any, repo_config: Any, pattern: str) -> list[str] | None:
    """The files already on the target's default branch, in the folder of the pattern of
    `adr_number_free` (`docs/adr/[0-9]...` gives `docs/adr`); `None` when the connector cannot list or
    fails: the guarantee then refuses rather than declare free a number it could not compare."""
    from tranibor_orchestrator.activities.repositories import repo_slug

    list_files = getattr(scm, "list_files", None)
    if repo_config is None or list_files is None:
        return None
    fixed = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
    folder = fixed.rsplit("/", 1)[0] if "/" in fixed else ""
    try:
        return list(await list_files(repo_slug(repo_config.url), repo_config.default_branch, folder))
    except Exception:
        return None


def added_text_of(patch: str) -> str:
    """The lines a unified patch ADDS, without their `+`: a new file, entire."""
    return "\n".join(
        line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")
    )


async def _publish_check_runs(scm: Any, repo: str, item: Any, outcomes: Any, head: str) -> None:
    """Check-runs `choregos/scope` and `choregos/evidence` (S3-05): the verdict is visible in the PR,
    on the work item's target repository (ADR 0124), on the commit it was given on (`head`, ADR
    0171) — never on a commit a run declared."""
    if not item.pr_url or not hasattr(scm, "create_check_run"):
        return
    for outcome in outcomes:
        name = CHECK_RUNS.get(outcome.name)
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


def provenance(checks: list[Any]) -> bool | None:
    """A finished provenance check on the PR (its name contains `provenance`): true if it succeeded.
    None, or not finished yet: we do not know, and the guarantee will say so when the CI is done."""
    finished = [c for c in checks if "provenance" in c.name.lower() and c.conclusion]
    if not finished:
        return None
    return all(c.conclusion == "success" for c in finished)


__all__ = [
    "CHECK_RUNS",
    "added_text_of",
    "empty_diff_of_unchanged_run",
    "head_of",
    "judged",
    "provenance",
    "read_delivery_facts",
]

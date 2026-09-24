"""Évaluation des gates : le mécanisme qui vérifie ce que l'agent affirme."""

from __future__ import annotations

from typing import Any

from choregos_api.db.models import Run
from choregos_contracts import StageResult
from choregos_core import GateContext, GateOutcome, evaluate, is_async_gate, matches_any, scan_secrets
from choregos_core.domain import PrRef
from temporalio import activity

from .base import db, load_work_item, project_bundle


@activity.defn(name="evaluate_gates")
async def evaluate_gates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Évalue les gates d'une transition contre le diff réel et l'état externe.

    Les gates synchrones tranchent tout de suite ; les asynchrones rendent `pending`
    tant que l'événement attendu (CI, review) n'est pas arrivé.
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
        needs_diff = any(g["name"] in {"scope_respected", "diff_size_max", "no_secrets"} for g in gates)
        # Sans dépôt, il n'y a pas de diff — et pas d'erreur non plus : la garantie refusera
        # d'elle-même (`diff_available`), ce qui est exactement ce qu'on veut qu'elle fasse.
        depot = bundle.config.repo
        if needs_diff and depot is not None:
            branch = (result.artifacts.branch if result else None) or bundle.config.branch_for(
                item.tracker_key
            )
            repo = _repo_slug(depot.url)
            try:
                diff = await bundle.adapters.scm.compare(repo, depot.default_branch, branch)
            except Exception:  # pas encore de branche : le diff est vide, pas une erreur
                diff = None
            if diff is not None:
                changed = diff.paths()
                additions, deletions = diff.additions, diff.deletions
                for file in diff.files:
                    if file.patch:
                        secrets.extend(scan_secrets(file.patch))

        ci_status = None
        review_state = None
        scans: dict[str, str] = {}
        if any(is_async_gate(g["name"]) for g in gates) and item.pr_url and depot is not None:
            repo = _repo_slug(depot.url)
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
                except Exception:
                    ci_status = None

        context = GateContext(
            result=result,
            changed_files=changed,
            additions=additions,
            deletions=deletions,
            allowed_paths=allowed,
            secrets_found=sorted(set(secrets)),
            ci_status=ci_status,
            review_state=review_state,
            scans=scans,
            flags=list(payload.get("flags", [])),
            expected_outputs=list(payload.get("expected_outputs", [])),
            # `needs_diff` dit qu'une garantie en dépend ; `diff` dit si on l'a obtenu.
            diff_available=(diff is not None) if needs_diff else True,
            required_flag=payload.get("required_flag"),
        )
        outcomes: list[GateOutcome] = []
        for gate in gates:
            params = dict(gate.get("params", {}))
            if gate["name"] == "diff_size_max" and "files" not in params:
                max_files, max_lines = bundle.engine.max_diff()
                params.setdefault("files", max_files)
                params.setdefault("lines", max_lines)
            outcomes.append(evaluate(gate["name"], context, params))

        await _publish_check_runs(bundle, item, outcomes, result)
        if run is not None:
            await _journaliser(session, run, outcomes)
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


async def _journaliser(session: Any, run: Run, outcomes: list[GateOutcome]) -> None:
    """Range chaque verdict dans le journal du run (`gate.outcome`).

    Les verdicts n'étaient rendus qu'au workflow, qui décidait puis les oubliait : aucun
    écran ne pouvait dire QUELLES garanties avaient tourné ni ce qu'elles avaient répondu —
    la promesse centrale du produit était invisible (état des lieux du 2026-09-24). Le
    journal du run est ce que la fiche affiche déjà ; les verdicts y prennent place.
    """
    from choregos_api.db.models import RunEvent
    from choregos_core import utcnow
    from sqlalchemy import func, select

    dernier = (
        await session.execute(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run.id))
    ).scalar()
    seq = int(dernier or 0)
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
    """Check-runs `choregos/scope` et `choregos/evidence` (S3-05) : le verdict est visible dans la PR."""
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
        depot = bundle.config.repo
        if not allowed or depot is None:
            # Sans dépôt, il n'y a pas de fichiers à comparer : aucune violation à signaler.
            # C'est bien une absence de matière, pas une absence de violation — et la
            # garantie `scope_respected`, elle, refuse (`diff_available`).
            return []
        repo = _repo_slug(depot.url)
        branch = bundle.config.branch_for(item.tracker_key)
        diff = await bundle.adapters.scm.compare(repo, depot.default_branch, branch)
        return [path for path in diff.paths() if not matches_any(path, allowed)]

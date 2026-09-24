"""Activités SCM : branche, PR, review, merge queue — pilotées par les transitions `system`."""

from __future__ import annotations

import json
from typing import Any

from choregos_api.db.models import Run
from choregos_contracts import StageResult
from choregos_core.domain import PrRef
from temporalio import activity

from .base import db, load_work_item, project_bundle
from .gates import _repo_slug


def _depot(bundle: Any) -> Any:
    """Le dépôt du projet, ou une erreur qui nomme la cause.

    Ces activités supposent un dépôt : ouvrir une PR, pousser une branche, lire des checks.
    Un projet sans dépôt (ADR 0012) n'a simplement aucune transition qui les déclenche —
    mais si une en déclenchait une, mieux vaut échouer ici, en disant pourquoi, que trois
    couches plus loin sur une URL vide.
    """
    if bundle.config.repo is None:
        raise ValueError(
            f"le projet {bundle.slug} n'a pas de dépôt : cette étape suppose un SCM "
            "(ouverture de PR, branche, checks). Retirer la transition, ou déclarer un dépôt."
        )
    return bundle.config.repo


@activity.defn(name="open_pull_request")
async def open_pull_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Ouvre (ou retrouve) la PR du ticket et y met le corps à jour — idempotent."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        repo = _repo_slug(_depot(bundle).url)
        branch = bundle.config.branch_for(item.tracker_key)
        base = _depot(bundle).default_branch
        documents = item.documents or {}
        body = _pr_body(item, documents)
        ref = await bundle.adapters.scm.open_pr(repo, branch, base, item.title, body, draft=False)
        await bundle.adapters.scm.update_pr(ref, body=body, draft=False)
        item.pr_url = ref.url or item.pr_url
        reviewers = payload.get("reviewers") or []
        if reviewers:
            await bundle.adapters.scm.request_review(ref, list(reviewers))
        return {"pr_url": item.pr_url, "number": ref.number}


def _pr_body(item: Any, documents: dict[str, Any]) -> str:
    parts = [
        f"Ticket : {item.tracker_key}",
        "",
        documents.get("spec_markdown", item.body_snapshot or ""),
    ]
    if documents.get("plan_markdown"):
        parts += ["", "## Plan", documents["plan_markdown"]]
    if documents.get("review_markdown"):
        parts += ["", "## Review agent", documents["review_markdown"]]
    parts += ["", "---", "_PR ouverte par Choregos ; le coût et les preuves sont dans le ticket._"]
    return "\n".join(parts)


@activity.defn(name="enqueue_merge")
async def enqueue_merge(payload: dict[str, Any]) -> dict[str, Any]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        if not item.pr_url:
            return {"enqueued": False, "reason": "aucune PR"}
        repo = _repo_slug(_depot(bundle).url)
        number = int(item.pr_url.rsplit("/", 1)[-1])
        await bundle.adapters.scm.enqueue_merge(PrRef(repo=repo, number=number))
        return {"enqueued": True, "pr_url": item.pr_url}


@activity.defn(name="ensure_branch")
async def ensure_branch(payload: dict[str, Any]) -> dict[str, str]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        repo = _repo_slug(_depot(bundle).url)
        branch = bundle.config.branch_for(item.tracker_key)
        await bundle.adapters.scm.ensure_branch(repo, branch, _depot(bundle).default_branch)
        return {"branch": branch}


@activity.defn(name="collect_run_artifacts")
async def collect_run_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    """Range le diff du run dans son résultat : c'est ce que le front affiche."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        run = await session.get(Run, payload["run_id"])
        if run is None or not run.result:
            return {"files": 0}
        repo = _repo_slug(_depot(bundle).url)
        branch = bundle.config.branch_for(item.tracker_key)
        try:
            diff = await bundle.adapters.scm.compare(repo, _depot(bundle).default_branch, branch)
        except Exception:
            return {"files": 0}
        result = StageResult.model_validate(run.result)
        result.artifacts.reports["diff_files"] = json.dumps(
            [
                {"path": f.path, "status": f.status, "additions": f.additions, "deletions": f.deletions}
                for f in diff.files
            ]
        )
        result.artifacts.reports["base"] = _depot(bundle).default_branch
        result.evidence.diff_files = len(diff.files)
        result.evidence.diff_lines = diff.additions + diff.deletions
        run.result = result.model_dump(mode="json", by_alias=True)
        return {"files": len(diff.files)}

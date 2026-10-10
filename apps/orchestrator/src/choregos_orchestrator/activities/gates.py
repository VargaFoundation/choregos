# SPDX-License-Identifier: Apache-2.0
"""Évaluation des gates : le mécanisme qui vérifie ce que l'agent affirme."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from choregos_api.db.models import CostLedger, Run
from choregos_contracts import StageResult
from choregos_core import GateContext, GateOutcome, evaluate, is_async_gate, matches_any, scan_secrets
from choregos_core.domain import PrRef
from choregos_core.gates import MOTIF_ADR
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
        ajoute: dict[str, str] = {}
        lisent_le_diff = {
            "scope_respected",
            "diff_size_max",
            "no_secrets",
            "markdown_sections",
            "adr_number_free",
        }
        needs_diff = any(g["name"] in lisent_le_diff for g in gates)
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
                        ajoute[file.path] = texte_ajoute(file.patch)

        ci_status = None
        review_state = None
        scans: dict[str, str] = {}
        signed: bool | None = None
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
                    signed = _provenance(pr.checks)
                except Exception:
                    ci_status = None

        # Ce que le run a réellement appelé au catalogue : le registre, pas le résultat.
        appels: list[str] = []
        if run is not None:
            from sqlalchemy import select

            rows = await session.execute(
                select(CostLedger.model).where(CostLedger.run_id == run.id, CostLedger.kind == "tool")
            )
            appels = [str(m) for m in rows.scalars() if m]
        # Le périmètre se juge sur ce que le RUN a écrit, depuis le commit où il a commencé (S22-11) :
        # depuis la base, il comptait le travail des étapes d'avant. Sur le locataire dev, le 09/10,
        # l'ADR de l'étude #5 rougissait sur le Makefile qu'avait poussé l'étape de cadrage.
        perimetre_du_run = await _diff_du_run(bundle, result, branch, gates) if diff is not None else None
        context = GateContext(
            result=result,
            tool_calls=appels,
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
            # `needs_diff` dit qu'une garantie en dépend ; `diff` dit si on l'a obtenu.
            diff_available=(diff is not None) if needs_diff else True,
            required_flag=payload.get("required_flag"),
            added_text=ajoute,
        )
        outcomes = [await _juger(gate, context, bundle, diff is not None, perimetre_du_run) for gate in gates]

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


async def _diff_du_run(
    bundle: Any, result: StageResult | None, branch: str, gates: list[dict[str, Any]]
) -> list[str] | None:
    """Les fichiers que le run a changés, depuis le commit où il a commencé ; `None` quand le run
    ne le dit pas (un runner d'avant S22-11) ou que la comparaison échoue : le diff du ticket vaut."""
    depart = (result.artifacts.reports.get("start_commit") if result else None) or ""
    if not depart or not any(g["name"] == "scope_respected" for g in gates) or bundle.config.repo is None:
        return None
    try:
        diff = await bundle.adapters.scm.compare(_repo_slug(bundle.config.repo.url), depart, branch)
    except Exception:
        return None
    return list(diff.paths())


async def _juger(
    gate: dict[str, Any],
    context: GateContext,
    bundle: Any,
    diff_obtenu: bool,
    perimetre_du_run: list[str] | None,
) -> GateOutcome:
    """Une garantie, avec ce qu'elle seule lit en plus du contexte commun."""
    params = dict(gate.get("params", {}))
    if gate["name"] == "diff_size_max" and "files" not in params:
        max_files, max_lines = bundle.engine.max_diff()
        params.setdefault("files", max_files)
        params.setdefault("lines", max_lines)
    if gate["name"] == "adr_number_free" and diff_obtenu:
        deja_la = await _fichiers_de_la_branche_par_defaut(bundle, params)
        return evaluate(gate["name"], replace(context, default_branch_files=deja_la), params)
    if gate["name"] == "scope_respected" and perimetre_du_run is not None:
        return evaluate(gate["name"], replace(context, changed_files=perimetre_du_run), params)
    return evaluate(gate["name"], context, params)


async def _fichiers_de_la_branche_par_defaut(bundle: Any, params: dict[str, Any]) -> list[str] | None:
    """Les fichiers déjà sur la branche par défaut, dans le dossier du motif de `adr_number_free`
    (`docs/adr/[0-9]…` → `docs/adr`) ; `None` quand le connecteur ne sait pas lister ou échoue :
    la garantie refuse alors plutôt que de déclarer libre un numéro qu'elle n'a pas pu comparer."""
    depot = bundle.config.repo
    lister = getattr(bundle.adapters.scm, "list_files", None)
    if depot is None or lister is None:
        return None
    motif = str(params.get("pattern") or MOTIF_ADR)
    fixe = re.split(r"[*?\[]", motif, maxsplit=1)[0]
    dossier = fixe.rsplit("/", 1)[0] if "/" in fixe else ""
    try:
        return list(await lister(_repo_slug(depot.url), depot.default_branch, dossier))
    except Exception:
        return None


def texte_ajoute(patch: str) -> str:
    """Les lignes qu'un patch unifié AJOUTE, sans leur `+` : un fichier neuf, tout entier."""
    return "\n".join(
        ligne[1:] for ligne in patch.splitlines() if ligne.startswith("+") and not ligne.startswith("+++")
    )


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


def _provenance(checks: list[Any]) -> bool | None:
    """Un check de provenance terminé sur la PR (son nom contient `provenance`) : vrai s'il a réussi.
    Aucun, ou pas encore terminé : on ne sait pas — la garantie le dira quand la CI aura fini."""
    termines = [c for c in checks if "provenance" in c.name.lower() and c.conclusion]
    if not termines:
        return None
    return all(c.conclusion == "success" for c in termines)

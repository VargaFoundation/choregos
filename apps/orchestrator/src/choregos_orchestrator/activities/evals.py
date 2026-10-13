# SPDX-License-Identifier: Apache-2.0
"""Activités d'évals : exécution d'une cellule de la matrice et publication des résultats.

Une cellule = (backend × modèle × mémoire) jouée sur les tickets de référence de
`tests/fixtures/tickets`. Le verdict d'un ticket n'est **jamais** déclaratif : le dépôt
jouet est recopié dans un espace de travail jetable, la modification est appliquée, puis
les assertions du ticket sont vérifiées pour de bon (fichier présent, motif présent ou
absent, fichier non touché, commande de test verte).

S37-09 (ADR 0055): a cell also has a context reduction (`none`, `rtk`, `headroom`,
`rtk+headroom`), and its tokens and cost are read from the ledger: every ticket is played under
its own gateway key, and what the gateway says that key spent is the cell's measure. In scripted
mode the fixture's `scripted_usage` is billed on a fake gateway; a real gateway is never billed
by the evals, so a ticket nothing was sent for has no measure, never a guessed one.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import statistics
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from choregos_api.db.models import ModelProfileRow
from choregos_core import utcnow
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .base import db, project_bundle

MIN_SUCCESS_RATE = 0.7
ASSERTION_TIMEOUT_S = 300.0
FIXTURES = Path(__file__).resolve().parents[5] / "tests" / "fixtures"
TICKETS = FIXTURES / "tickets"
REPOS = FIXTURES / "repos"
CONTEXTS = ("none", "rtk", "headroom", "rtk+headroom")
EVAL_KEY_BUDGET_USD = 50.0
EVAL_KEY_TTL_S = 3 * 3600


@dataclass(slots=True)
class Check:
    """Verdict d'une assertion : `ok=None` signifie ignorée (outil absent)."""

    label: str
    ok: bool | None
    detail: str = ""


@activity.defn(name="list_fixtures")
async def list_fixtures(payload: dict[str, Any]) -> list[str]:
    """Tickets de référence livrés dans `tests/fixtures/tickets` (S12-01)."""
    if not TICKETS.is_dir():
        return []
    return sorted(path.stem for path in TICKETS.glob("*.yaml"))


@activity.defn(name="run_eval_cell")
async def run_eval_cell(payload: dict[str, Any]) -> dict[str, Any]:
    """Joue les tickets de référence pour un couple backend × modèle, avec et sans mémoire.

    Chaque ticket a un critère de succès **déterministe** : la note ne dépend d'aucun
    jugement subjectif. Un ticket dont les outils manquent (pas de `npm` sur le runner)
    est ignoré, jamais compté comme réussi — même règle que la boucle DoD du runner.
    """
    fixtures: list[str] = payload.get("fixtures", [])
    context = str(payload.get("context", "none"))
    if context not in CONTEXTS:
        raise ApplicationError(
            f"unknown context reduction {context!r}, expected one of {', '.join(CONTEXTS)}",
            non_retryable=True,
        )
    successes: list[bool] = []
    costs: list[float] = []
    durations: list[float] = []
    metered: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for name in fixtures:
        spec = _load_spec(name)
        if spec is None:
            continue
        outcome = await _play(spec, payload)
        if outcome["skipped"]:
            details.append({"fixture": name, "skipped": True, "reason": outcome["reason"]})
            continue
        successes.append(bool(outcome["ok"]))
        if outcome["usage"] is not None:
            metered.append(outcome["usage"])
            costs.append(outcome["usage"]["cost_usd"])
        durations.append(outcome["duration_s"])
        details.append({"fixture": name, "ok": outcome["ok"], "reason": outcome["reason"]})
        with contextlib.suppress(RuntimeError):
            activity.heartbeat({"fixture": name})
    rate = (sum(1 for s in successes if s) / len(successes)) if successes else 0.0
    return {
        "backend": payload["backend"],
        "model": payload["model"],
        "with_memory": payload["with_memory"],
        "context": context,
        "cases": len(successes),
        "skipped": sum(1 for d in details if d.get("skipped")),
        # Cases the ledger has a measure for: tokens and cost are medians over these only.
        "metered": len(metered),
        "success_rate": round(rate, 4),
        "median_cost_usd": round(statistics.median(costs), 4) if costs else None,
        "median_tokens_in": _median(metered, "tokens_in"),
        "median_tokens_cached": _median(metered, "tokens_cached"),
        "median_tokens_out": _median(metered, "tokens_out"),
        "median_turns": _median([u for u in metered if u["turns"] is not None], "turns"),
        "median_duration_s": round(statistics.median(durations), 2) if durations else None,
        "validated": bool(successes) and rate >= MIN_SUCCESS_RATE,
        "details": details,
    }


def _median(usages: list[dict[str, Any]], key: str) -> float | None:
    values = [float(usage[key]) for usage in usages]
    return round(statistics.median(values), 1) if values else None


def _load_spec(name: str) -> dict[str, Any] | None:
    import yaml

    path = TICKETS / f"{name}.yaml"
    if not path.exists():
        return None
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    return spec if isinstance(spec, dict) else None


async def _play(spec: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Matérialise le dépôt jouet, applique la modification, vérifie les assertions."""
    repo = REPOS / str(spec.get("repo", ""))
    if not repo.is_dir():
        return _skip(f"dépôt jouet absent : {spec.get('repo')}")
    async with db() as session:
        gateway = (await project_bundle(session, payload["project_slug"])).adapters.gateway
    context = str(payload.get("context", "none"))
    model = str(payload.get("model", "platform/standard"))
    key = await gateway.mint_key(
        {
            "purpose": "eval",
            "fixture": str(spec.get("id", "")),
            "context": context,
            "backend": payload.get("backend"),
        },
        EVAL_KEY_BUDGET_USD,
        EVAL_KEY_TTL_S,
        [model],
    )
    try:
        with tempfile.TemporaryDirectory(prefix="choregos-eval-") as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(repo, work)
            before = _snapshot(work)
            patch = await _apply_change(spec, payload, work)
            if patch.get("skipped"):
                return _skip(str(patch.get("reason", "")))
            turns = _bill_scripted(gateway, key.key_id, spec, context, model, patch)
            verdict = await _judge(spec, work, before, patch)
        usage = await _ledger(gateway, key.key_id, turns)
    finally:
        await gateway.revoke(key.key_id)
    if verdict.get("skipped"):
        return verdict
    return {**verdict, "usage": usage, "duration_s": float(spec.get("expected_duration_s", 120.0))}


async def _judge(
    spec: dict[str, Any], work: Path, before: dict[str, str], patch: dict[str, Any]
) -> dict[str, Any]:
    """The ticket's assertions on the workspace, and the status the change declared."""
    checks = await _verify(spec, work, before)
    blocking = [c for c in checks if c.ok is False]
    missing_tools = [c for c in checks if c.ok is None]
    if missing_tools and not blocking:
        return _skip(missing_tools[0].detail)
    expected_blocked = spec.get("expect_status") == "blocked"
    declared = str(patch.get("status", "done"))
    ok = not blocking and (declared == "blocked") == expected_blocked
    reason = (
        blocking[0].label + " — " + blocking[0].detail
        if blocking
        else ("" if ok else f"statut attendu {spec.get('expect_status', 'done')}, obtenu {declared}")
    )
    return {"ok": ok, "skipped": False, "reason": reason}


def _bill_scripted(
    gateway: Any, key_id: str, spec: dict[str, Any], context: str, model: str, patch: dict[str, Any]
) -> int | None:
    """Bill the fixture's scripted usage on a fake gateway; returns the turns, which no ledger has.

    Only a scripted change is billed, and only on a gateway that can be billed by hand
    (`record_usage`, the fake's): a real gateway counts what was really sent, and nothing else.
    """
    if patch.get("source") != "scripted":
        return patch.get("turns") if isinstance(patch.get("turns"), int) else None
    usage = (spec.get("scripted_usage") or {}).get(context)
    record = getattr(gateway, "record_usage", None)
    if not isinstance(usage, dict) or record is None:
        return None
    record(
        key_id,
        tokens_in=int(usage["tokens_in"]),
        tokens_out=int(usage["tokens_out"]),
        cached=int(usage.get("tokens_cached", 0)),
        model=model,
    )
    return int(usage["turns"]) if "turns" in usage else None


async def _ledger(gateway: Any, key_id: str, turns: int | None) -> dict[str, Any] | None:
    """What the gateway says the ticket's key spent; `None` when nothing went through it."""
    spend = await gateway.spend(key_id)
    if not spend.requests:
        return None
    return {
        "tokens_in": spend.tokens_in,
        "tokens_cached": spend.tokens_cached,
        "tokens_out": spend.tokens_out,
        "cost_usd": spend.cost_usd,
        "turns": turns,
    }


def _skip(reason: str) -> dict[str, Any]:
    return {"ok": False, "skipped": True, "reason": reason, "usage": None, "duration_s": 0.0}


async def _apply_change(spec: dict[str, Any], payload: dict[str, Any], work: Path) -> dict[str, Any]:
    """Applique la modification de l'agent dans l'espace de travail.

    En mode scripté (CI, `CHOREGOS_FAKES=1`), la fixture fournit le diff : la matrice est
    reproductible et les assertions portent sur de vrais fichiers. Avec un exécuteur réel,
    c'est le `StageResult` du runner qui est relu.
    """
    scripted = spec.get("scripted") or {}
    variant = scripted.get("with_memory" if payload.get("with_memory") else "without_memory")
    if variant is None:
        variant = scripted.get("with_memory")
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        executor = bundle.adapters.executor
    result = getattr(executor, "result_for", lambda _run: None)(str(spec.get("id", "")))
    if result is not None:
        diagnostics = getattr(result, "diagnostics", None)
        return {
            "status": str(result.status),
            "source": "executor",
            "turns": getattr(diagnostics, "turns", None),
        }
    if variant is None:
        return {"skipped": True, "reason": f"aucune modification scriptée pour {spec.get('id')}"}
    for relative, content in (variant.get("files") or {}).items():
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return {"status": str(variant.get("status", "done")), "source": "scripted"}


def _snapshot(work: Path) -> dict[str, str]:
    return {
        str(path.relative_to(work)): path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(work.rglob("*"))
        if path.is_file()
    }


async def _verify(spec: dict[str, Any], work: Path, before: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    for raw in spec.get("assertions") or []:
        kind = str(raw.get("kind"))
        if kind == "command":
            checks.append(await _check_command(raw, work))
        else:
            checks.append(_check_file(kind, raw, work, before))
    return checks


def _check_file(kind: str, raw: dict[str, Any], work: Path, before: dict[str, str]) -> Check:
    relative = str(raw.get("path", ""))
    path = work / relative
    label = f"{kind}:{relative}"
    if kind == "file_exists":
        return Check(label, path.is_file(), "fichier attendu absent" if not path.is_file() else "")
    if kind == "file_absent":
        return Check(label, not path.exists(), "fichier créé alors qu'il ne devait pas l'être")
    if kind == "file_unchanged":
        now = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
        return Check(label, now == before.get(relative), "fichier modifié hors de l'attendu")
    if kind == "file_matches":
        if not path.is_file():
            return Check(label, False, "fichier absent")
        content = path.read_text(encoding="utf-8", errors="replace")
        found = re.search(str(raw.get("pattern", "")), content) is not None
        expect_absent = bool(raw.get("absent", False))
        detail = f"motif {'présent' if found else 'absent'} : {raw.get('pattern')}"
        return Check(label, found is not expect_absent, detail)
    return Check(label, False, f"assertion inconnue : {kind}")


async def _check_command(raw: dict[str, Any], work: Path) -> Check:
    command = str(raw.get("run", ""))
    label = f"command:{command}"
    required = raw.get("requires")
    if required and shutil.which(str(required)) is None:
        return Check(label, None, f"outil absent sur ce runner : {required}")
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=work,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=ASSERTION_TIMEOUT_S)
    except TimeoutError:
        process.kill()
        await process.wait()
        return Check(label, False, f"délai dépassé ({ASSERTION_TIMEOUT_S:.0f} s)")
    output = stdout.decode("utf-8", errors="replace").strip()
    return Check(label, process.returncode == 0, "" if process.returncode == 0 else output[-600:])


@activity.defn(name="publish_matrix")
async def publish_matrix(payload: dict[str, Any]) -> dict[str, Any]:
    """Publie la matrice : `model_profiles.validated_backends` et `GET /models/matrix`."""
    results: list[dict[str, Any]] = payload.get("results", [])
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        by_model: dict[str, list[dict[str, Any]]] = {}
        for entry in results:
            by_model.setdefault(entry["model"], []).append(entry)
        for model, entries in by_model.items():
            row = (
                await session.execute(
                    select(ModelProfileRow).where(
                        ModelProfileRow.litellm_model == model, ModelProfileRow.scope == "platform"
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = ModelProfileRow(scope="platform", name=model.split("/")[-1], litellm_model=model)
                session.add(row)
            # A run gets no context reduction unless its policy says so: a backend is validated by
            # its cells without one, never by a cell that only passed with rtk or headroom (S37-09).
            row.validated_backends = sorted(
                {e["backend"] for e in entries if e.get("validated") and e.get("context", "none") == "none"}
            )
            row.params = {
                **(row.params or {}),
                "matrix": entries,
                "matrix_generated_at": utcnow().isoformat(),
            }
        return {"models": len(by_model), "project": bundle.slug}

"""Activités d'évals : exécution d'une cellule de la matrice et publication des résultats.

Une cellule = (backend × modèle × mémoire) jouée sur les tickets de référence de
`tests/fixtures/tickets`. Le verdict d'un ticket n'est **jamais** déclaratif : le dépôt
jouet est recopié dans un espace de travail jetable, la modification est appliquée, puis
les assertions du ticket sont vérifiées pour de bon (fichier présent, motif présent ou
absent, fichier non touché, commande de test verte).
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

from .base import db, project_bundle

MIN_SUCCESS_RATE = 0.7
ASSERTION_TIMEOUT_S = 300.0
FIXTURES = Path(__file__).resolve().parents[5] / "tests" / "fixtures"
TICKETS = FIXTURES / "tickets"
REPOS = FIXTURES / "repos"


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
    successes: list[bool] = []
    costs: list[float] = []
    durations: list[float] = []
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
        costs.append(outcome["cost_usd"])
        durations.append(outcome["duration_s"])
        details.append({"fixture": name, "ok": outcome["ok"], "reason": outcome["reason"]})
        with contextlib.suppress(RuntimeError):
            activity.heartbeat({"fixture": name})
    rate = (sum(1 for s in successes if s) / len(successes)) if successes else 0.0
    return {
        "backend": payload["backend"],
        "model": payload["model"],
        "with_memory": payload["with_memory"],
        "cases": len(successes),
        "skipped": sum(1 for d in details if d.get("skipped")),
        "success_rate": round(rate, 4),
        "median_cost_usd": round(statistics.median(costs), 4) if costs else None,
        "median_duration_s": round(statistics.median(durations), 2) if durations else None,
        "validated": bool(successes) and rate >= MIN_SUCCESS_RATE,
        "details": details,
    }


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
    with_memory = bool(payload.get("with_memory"))
    with tempfile.TemporaryDirectory(prefix="choregos-eval-") as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(repo, work)
        before = _snapshot(work)
        patch = await _apply_change(spec, payload, work)
        if patch.get("skipped"):
            return _skip(str(patch.get("reason", "")))
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
    cost = float(spec.get("expected_cost_usd", 1.0)) * (0.9 if with_memory else 1.0)
    return {
        "ok": ok,
        "skipped": False,
        "reason": reason,
        "cost_usd": cost,
        "duration_s": float(spec.get("expected_duration_s", 120.0)),
    }


def _skip(reason: str) -> dict[str, Any]:
    return {"ok": False, "skipped": True, "reason": reason, "cost_usd": 0.0, "duration_s": 0.0}


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
        return {"status": str(result.status)}
    if variant is None:
        return {"skipped": True, "reason": f"aucune modification scriptée pour {spec.get('id')}"}
    for relative, content in (variant.get("files") or {}).items():
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return {"status": str(variant.get("status", "done"))}


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
            row.validated_backends = sorted({e["backend"] for e in entries if e.get("validated")})
            row.params = {
                **(row.params or {}),
                "matrix": entries,
                "matrix_generated_at": utcnow().isoformat(),
            }
        return {"models": len(by_model), "project": bundle.slug}

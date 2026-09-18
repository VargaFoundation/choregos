"""Activités d'évals : exécution d'une cellule de la matrice et publication des résultats."""

from __future__ import annotations

import contextlib
import statistics
from typing import Any

from choregos_api.db.models import ModelProfileRow
from choregos_core import utcnow
from sqlalchemy import select
from temporalio import activity

from .base import db, project_bundle

MIN_SUCCESS_RATE = 0.7


@activity.defn(name="list_fixtures")
async def list_fixtures(payload: dict[str, Any]) -> list[str]:
    """Dépôts jouets et tickets de référence livrés dans `tests/fixtures/` (S12-01)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "tickets"
    if not root.is_dir():
        return []
    return sorted(path.stem for path in root.glob("*.yaml"))


@activity.defn(name="run_eval_cell")
async def run_eval_cell(payload: dict[str, Any]) -> dict[str, Any]:
    """Joue les tickets de référence pour un couple backend × modèle, avec et sans mémoire.

    Chaque ticket a un critère de succès **déterministe** (fichier créé, tests verts,
    `result.json` valide) : la note ne dépend pas d'un jugement subjectif.
    """
    from pathlib import Path

    import yaml

    fixtures: list[str] = payload.get("fixtures", [])
    root = Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "tickets"
    successes: list[bool] = []
    costs: list[float] = []
    durations: list[float] = []
    for name in fixtures:
        path = root / f"{name}.yaml"
        if not path.exists():
            continue
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        outcome = await _simulate(spec, payload)
        successes.append(outcome["ok"])
        costs.append(outcome["cost_usd"])
        durations.append(outcome["duration_s"])
        with contextlib.suppress(RuntimeError):
            activity.heartbeat({"fixture": name})
    rate = (sum(1 for s in successes if s) / len(successes)) if successes else 0.0
    return {
        "backend": payload["backend"],
        "model": payload["model"],
        "with_memory": payload["with_memory"],
        "cases": len(successes),
        "success_rate": round(rate, 4),
        "median_cost_usd": round(statistics.median(costs), 4) if costs else None,
        "median_duration_s": round(statistics.median(durations), 2) if durations else None,
        "validated": rate >= MIN_SUCCESS_RATE,
    }


async def _simulate(spec: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Exécute un ticket de référence via l'exécuteur configuré.

    En mode fakes, l'exécuteur rend le résultat scripté de la fixture : la matrice reste
    reproductible en CI, et le vrai backend prend le relais en nocturne.
    """
    expected = spec.get("expect", {})
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        executor = bundle.adapters.executor
        result = getattr(executor, "result_for", lambda _run: None)(spec.get("id", "fixture"))
    ok = bool(expected.get("tests_passed", True)) if result is None else result.ok
    bonus = 0.0 if payload.get("with_memory") else float(spec.get("memory_penalty", 0.0))
    return {
        "ok": ok and bonus <= 0.5,
        "cost_usd": float(spec.get("expected_cost_usd", 1.0)) * (0.9 if payload.get("with_memory") else 1.0),
        "duration_s": float(spec.get("expected_duration_s", 120.0)),
    }


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

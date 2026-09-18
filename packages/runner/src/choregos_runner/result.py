"""Lecture, validation et réparation de `.choregos/result.json`.

L'agent écrit ce fichier ; le runner le valide contre le contrat, demande une réparation
si besoin, puis le complète avec ce qu'il a **mesuré** (preuves, artefacts, diagnostics).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from choregos_contracts import (
    Artifacts,
    Diagnostics,
    Evidence,
    StageResult,
    StageStatus,
)
from pydantic import ValidationError

from .dod import merge_evidence


@dataclass(slots=True)
class ResultLoad:
    """Issue d'une tentative de lecture du résultat."""

    result: StageResult | None
    error: str | None = None
    raw: str | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None


def load_result(path: Path) -> ResultLoad:
    """Lit et valide le fichier. Toute erreur est formulée pour être renvoyée à l'agent."""
    if not path.exists():
        return ResultLoad(None, error=f"`{path.name}` est absent : écris-le avant de terminer.")
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return ResultLoad(None, error=f"`{path.name}` est vide.", raw=raw)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ResultLoad(
            None, error=f"JSON invalide ligne {exc.lineno}, colonne {exc.colno} : {exc.msg}", raw=raw
        )
    if not isinstance(payload, dict):
        return ResultLoad(None, error="le document doit être un objet JSON.", raw=raw)
    payload.setdefault("schema", "choregos/StageResult/v1")
    try:
        return ResultLoad(StageResult.model_validate(payload), raw=raw)
    except ValidationError as exc:
        return ResultLoad(None, error=_explain(exc), raw=raw)


def _explain(exc: ValidationError) -> str:
    lines = ["le résultat ne respecte pas le contrat `choregos/StageResult/v1` :"]
    for error in exc.errors()[:8]:
        location = ".".join(str(part) for part in error["loc"]) or "(racine)"
        lines.append(f"- `{location}` : {error['msg']}")
    return "\n".join(lines)


def repair_prompt(load: ResultLoad, path: Path) -> str:
    """Message envoyé à l'agent pour qu'il corrige son résultat."""
    return "\n".join(
        [
            f"Ton fichier `{path.name}` n'est pas exploitable.",
            "",
            load.error or "raison inconnue",
            "",
            "Réécris-le entièrement, sans commentaire autour, avec au minimum :",
            "```json",
            json.dumps(
                {
                    "schema": "choregos/StageResult/v1",
                    "status": "done",
                    "summary": "ce que tu as fait, en une phrase",
                    "evidence": {"tests_passed": True, "tests_run": 0},
                },
                indent=2,
                ensure_ascii=False,
            ),
            "```",
        ]
    )


def fallback_result(reason: str, summary: str) -> StageResult:
    """Résultat produit par le runner quand l'agent n'a pas pu en produire un valide."""
    return StageResult(status=StageStatus.FAILED, summary=summary, reason=reason)


def complete_result(
    result: StageResult,
    *,
    measured: Evidence,
    artifacts: Artifacts,
    diagnostics: Diagnostics,
    scope_blocked: list[str] | None = None,
) -> StageResult:
    """Complète le résultat de l'agent avec ce que le runner a observé.

    Les preuves mesurées écrasent les preuves déclarées : c'est le mécanisme qui empêche
    un agent d'affirmer que les tests passent alors qu'ils échouent.
    """
    data: dict[str, Any] = result.model_dump(by_alias=True)
    data["evidence"] = merge_evidence(result.evidence, measured).model_dump()
    merged_artifacts = result.artifacts.model_dump()
    for key, value in artifacts.model_dump().items():
        if value in (None, [], {}):
            continue
        if key == "reports":
            merged_artifacts["reports"] = {**merged_artifacts.get("reports", {}), **value}
        else:
            merged_artifacts[key] = value
    data["artifacts"] = merged_artifacts
    data["diagnostics"] = diagnostics.model_dump()

    completed = StageResult.model_validate(data)
    if scope_blocked:
        completed.status = StageStatus.BLOCKED
        completed.reason = "scope"
        completed.summary = (
            f"{completed.summary} — {len(scope_blocked)} fichier(s) hors périmètre "
            f"non annulables : {', '.join(scope_blocked[:5])}"
        )
    elif completed.status is StageStatus.DONE and completed.evidence.tests_passed is False:
        completed.status = StageStatus.FAILED
        completed.reason = "tests"
        completed.summary = f"{completed.summary} — les tests du dépôt échouent"
    return completed


def write_result(path: Path, result: StageResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.model_dump(mode="json", by_alias=True), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

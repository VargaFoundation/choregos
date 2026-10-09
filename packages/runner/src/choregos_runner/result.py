# SPDX-License-Identifier: Apache-2.0
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


def load_result(path: Path, declarees: list[str] | None = None) -> ResultLoad:
    """Lit et valide le fichier. Toute erreur est formulée pour être renvoyée à l'agent.

    `declarees` : les sorties que la transition déclare. Un résultat `done` qui en omet une
    repart en réparation (S22-12) — sur le locataire dev, le 09/10, un triage rendait
    une sortie `triage` (du JSON en texte) au lieu de `size` et `risk`, et la garde `outputs_in`
    escaladait un ticket qui était, en fait, triable."""
    if not path.exists():
        return ResultLoad(None, error=f"`{path.name}` is missing: write it before you finish.")
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return ResultLoad(None, error=f"`{path.name}` is empty.", raw=raw)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ResultLoad(
            None, error=f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}", raw=raw
        )
    if not isinstance(payload, dict):
        return ResultLoad(None, error="the document must be a JSON object.", raw=raw)
    payload.setdefault("schema", "choregos/StageResult/v1")
    try:
        resultat = StageResult.model_validate(payload)
    except ValidationError as exc:
        return ResultLoad(None, error=_explain(exc), raw=raw)
    manquantes = _sorties_manquantes(resultat, declarees or [])
    if manquantes:
        attendues = ", ".join(f"`{nom}`" for nom in declarees or [])
        return ResultLoad(
            None,
            error=(
                f"`outputs` lacks {', '.join(f'`{nom}`' for nom in manquantes)}: this step declares "
                f"{attendues}, each a key of `outputs` of its own — not nested in another output."
            ),
            raw=raw,
        )
    return ResultLoad(resultat, raw=raw)


def _sorties_manquantes(resultat: StageResult, declarees: list[str]) -> list[str]:
    """Les sorties déclarées qu'un résultat `done` n'écrit pas. Une étape qui ne finit pas —
    une question, un échec — n'a pas à les rendre."""
    if resultat.status is not StageStatus.DONE:
        return []
    ecrites = resultat.outputs.model_dump(exclude_none=True)
    return [nom for nom in declarees if ecrites.get(nom) in (None, "", [], {})]


def _explain(exc: ValidationError) -> str:
    lines = ["the result does not follow the `choregos/StageResult/v1` contract:"]
    for error in exc.errors()[:8]:
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"- `{location}`: {error['msg']}")
    return "\n".join(lines)


def repair_prompt(load: ResultLoad, path: Path) -> str:
    """Message envoyé à l'agent pour qu'il corrige son résultat."""
    return "\n".join(
        [
            f"Your file `{path.name}` cannot be used.",
            "",
            load.error or "unknown reason",
            "",
            "Rewrite it entirely, with no comment around it, with at least:",
            "```json",
            json.dumps(
                {
                    "schema": "choregos/StageResult/v1",
                    "status": "done",
                    "summary": "what you did, in one sentence",
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
            f"{completed.summary} — {len(scope_blocked)} out-of-scope file(s) "
            f"that could not be reverted: {', '.join(scope_blocked[:5])}"
        )
    elif completed.status is StageStatus.DONE and completed.evidence.tests_passed is False:
        completed.status = StageStatus.FAILED
        completed.reason = "tests"
        completed.summary = f"{completed.summary} — the repository's tests fail"
    return completed


def write_result(path: Path, result: StageResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.model_dump(mode="json", by_alias=True), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

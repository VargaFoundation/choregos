# SPDX-License-Identifier: Apache-2.0
"""Read, validate and repair `.choregos/result.json`.

The agent writes this file; the runner validates it against the contract, asks for a repair
if needed, then completes it with what it **measured** (evidence, artifacts, diagnostics).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from choregos_contracts import (
    CHECKED_TYPES,
    ArtifactDisplay,
    Artifacts,
    Diagnostics,
    Evidence,
    StageResult,
    StageStatus,
    coerce_value,
)
from pydantic import ValidationError

from .dod import merge_evidence


@dataclass(slots=True)
class ResultLoad:
    """Outcome of an attempt to read the result."""

    result: StageResult | None
    error: str | None = None
    raw: str | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None


def load_result(
    path: Path,
    declared: list[str] | None = None,
    files: list[str] | None = None,
    displays: dict[str, ArtifactDisplay] | None = None,
) -> ResultLoad:
    """Read and validate the file. Every error is worded to be sent back to the agent.

    `declared`: the outputs the transition declares. A `done` result that omits one
    goes back to repair (S22-12): on the dev tenant, on 09/10, a triage returned
    a `triage` output (JSON as text) instead of `size` and `risk`, and the `outputs_in` guard
    escalated a ticket that was, in fact, triageable."""
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
        parsed = StageResult.model_validate(payload)
    except ValidationError as exc:
        return ResultLoad(None, error=_explain(exc), raw=raw)
    missing = _missing_outputs(parsed, declared or [])
    if missing:
        expected = ", ".join(f"`{name}`" for name in declared or [])
        return ResultLoad(
            None,
            error=(
                f"`outputs` lacks {', '.join(f'`{name}`' for name in missing)}: this step declares "
                f"{expected}, each a key of `outputs` of its own — not nested in another output."
            ),
            raw=raw,
        )
    # A value off its declared type goes back like a missing one (ADR 0129 §10).
    off_type = _off_type(parsed, displays or {})
    if off_type:
        return ResultLoad(
            None, error="\n".join(["`outputs` holds a value of the wrong kind:", *off_type]), raw=raw
        )
    # `files`: the deliverables a `done` step declares (ADR 0049), expected beside this file in
    # `.choregos/outputs/`. A missing one goes back to the agent like a missing output.
    absent = [name for name in files or [] if not (path.parent / "outputs" / name).is_file()]
    if absent and parsed.status is StageStatus.DONE:
        return ResultLoad(
            None,
            error=(
                f"missing file(s) in `.choregos/outputs/`: {', '.join(f'`{name}`' for name in absent)}. "
                "This step declares them as deliverables: write each file there, then rewrite the result."
            ),
            raw=raw,
        )
    return ResultLoad(parsed, raw=raw)


def _missing_outputs(parsed: StageResult, declared: list[str]) -> list[str]:
    """The declared outputs that a `done` result does not write. A step that does not finish,
    a question or a failure, does not have to return them."""
    if parsed.status is not StageStatus.DONE:
        return []
    written = parsed.outputs.model_dump(exclude_none=True)
    return [name for name in declared if written.get(name) in (None, "", [], {})]


def _off_type(parsed: StageResult, displays: dict[str, ArtifactDisplay]) -> list[str]:
    """What is wrong with the typed outputs of a `done` result, one line per output, for the agent."""
    if parsed.status is not StageStatus.DONE:
        return []
    written = parsed.outputs.model_dump(exclude_none=True)
    problems: list[str] = []
    for name, display in displays.items():
        if name not in written or display.type not in CHECKED_TYPES:
            continue
        try:
            coerce_value(display, written[name])
        except ValueError as error:
            problems.append(f"- `{name}`: {error}.")
    return problems


def _explain(exc: ValidationError) -> str:
    lines = ["the result does not follow the `choregos/StageResult/v1` contract:"]
    for error in exc.errors()[:8]:
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"- `{location}`: {error['msg']}")
    return "\n".join(lines)


def repair_prompt(load: ResultLoad, path: Path) -> str:
    """Message sent to the agent so that it fixes its result."""
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
    """Result produced by the runner when the agent could not produce a valid one."""
    return StageResult(status=StageStatus.FAILED, summary=summary, reason=reason)


def complete_result(
    result: StageResult,
    *,
    measured: Evidence,
    artifacts: Artifacts,
    diagnostics: Diagnostics,
    scope_blocked: list[str] | None = None,
) -> StageResult:
    """Complete the agent's result with what the runner observed.

    Measured evidence overrides declared evidence: this is the mechanism that prevents
    an agent from claiming the tests pass when they fail.
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

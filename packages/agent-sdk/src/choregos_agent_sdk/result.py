# SPDX-License-Identifier: Apache-2.0
"""The step's result: what the agent returns, written as `.choregos/result.json`.

The file is exactly what the runner reads (`choregos/StageResult/v1`); the SDK validates it with the
contract's own model before writing, so a result the runner would send back for repair never leaves
the agent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from choregos_contracts import StageResult

Status = Literal["done", "failed", "blocked", "needs_human"]

#: The `summary` of a result is 1 to 4000 characters.
SUMMARY_MAX = 4000


@dataclass
class Result:
    """What a step produced.

    `facts` are named plain values (text, number, boolean) the workflow's gates can read; `outputs`
    are the step's declared outputs (a key per declared output). Files are written with
    `ctx.write_output`, not returned.
    """

    summary: str
    status: Status = "done"
    #: Why the step is not `done`, as a short code (`max_turns`, `permission_denied`, ...).
    reason: str | None = None
    facts: dict[str, str | int | float | bool] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    #: Questions for a human, with `status="needs_human"`.
    questions: list[str] = field(default_factory=list)

    def to_stage_result(self) -> StageResult:
        payload: dict[str, Any] = {
            "schema": "choregos/StageResult/v1",
            "status": self.status,
            "summary": (self.summary.strip() or "(no summary)")[:SUMMARY_MAX],
            "outputs": dict(self.outputs),
            "evidence": {"facts": dict(self.facts)} if self.facts else {},
            "questions": [{"text": q} for q in self.questions],
        }
        if self.reason:
            payload["reason"] = self.reason
        return StageResult.model_validate(payload)


def failure(summary: str, reason: str) -> Result:
    return Result(summary=summary, status="failed", reason=reason)


def write_result(workspace: Path, result: Result) -> Path:
    """Validates, then writes `.choregos/result.json`. A result the contract refuses raises."""
    stage_result = result.to_stage_result()
    path = workspace / ".choregos" / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = stage_result.model_dump(mode="json", by_alias=True, exclude_none=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

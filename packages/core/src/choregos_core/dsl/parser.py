# SPDX-License-Identifier: Apache-2.0
"""Parsing a workflow: YAML/JSON → validated model, with located errors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from choregos_contracts import Workflow
from pydantic import ValidationError as PydanticValidationError

from ..errors import Issue, ValidationError
from .validator import ValidationReport, validate_workflow
from .yamlsource import json_pointer, load_yaml, locate


def checksum(document: Any) -> str:
    """Stable fingerprint of a workflow (the pinning key on a work item)."""
    if isinstance(document, Workflow):
        payload = document.model_dump(mode="json", by_alias=True, exclude_none=True)
    elif isinstance(document, str):
        payload = yaml.safe_load(document)
    else:
        payload = document
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _pydantic_issues(exc: PydanticValidationError, source: Any) -> list[Issue]:
    issues: list[Issue] = []
    for error in exc.errors():
        path = [p for p in error["loc"] if isinstance(p, (str, int))]
        # pydantic names the aliased field `from_`; the YAML says `from`
        path = ["from" if p == "from_" else p for p in path]
        line, column = locate(source, list(path)) if source is not None else (None, None)
        issues.append(
            Issue(
                code=f"schema.{error['type']}",
                message=error["msg"],
                path=json_pointer(list(path)),
                line=line,
                column=column,
            )
        )
    return issues


def parse_workflow(text: str, *, strict: bool = True) -> tuple[Workflow, ValidationReport]:
    """Parse a YAML/JSON workflow and apply the static rules.

    `strict=True` raises `ValidationError` if the document is invalid; otherwise the
    function returns the report and the workflow is guaranteed syntactically valid.
    """
    try:
        source = load_yaml(text)
    except yaml.YAMLError as exc:  # broken YAML syntax
        mark = getattr(exc, "problem_mark", None)
        issue = Issue(
            code="yaml.syntax",
            message=str(getattr(exc, "problem", exc)),
            path=None,
            line=(mark.line + 1) if mark else None,
            column=(mark.column + 1) if mark else None,
        )
        raise ValidationError([issue], subject="workflow") from exc

    if not isinstance(source, dict):
        raise ValidationError(
            [Issue("yaml.not_a_mapping", "the document must be a YAML object", None)], subject="workflow"
        )

    document = dict(source)
    # The order of the keys is true HERE, and nowhere else: so the start is written into the
    # document before any serialisation. An `initial` already written is respected; the validator
    # will say whether it names a known, non-terminal state.
    states = document.get("states")
    if not document.get("initial") and isinstance(states, dict) and states:
        document["initial"] = next(iter(states))

    try:
        workflow = Workflow.model_validate(document)
    except PydanticValidationError as exc:
        raise ValidationError(_pydantic_issues(exc, source), subject="workflow") from exc

    report = validate_workflow(workflow, source)
    if strict and not report.valid:
        raise ValidationError(report.errors, subject="workflow")
    return workflow, report


def parse_workflow_file(path: str | Path, *, strict: bool = True) -> tuple[Workflow, ValidationReport]:
    return parse_workflow(Path(path).read_text(encoding="utf-8"), strict=strict)


def dump_workflow(workflow: Workflow) -> str:
    """Serialise a workflow as canonical YAML (key order kept)."""
    payload = workflow.model_dump(mode="json", by_alias=True, exclude_none=True)
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=120)

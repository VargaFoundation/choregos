# SPDX-License-Identifier: Apache-2.0
"""Workflow DSL: loading, validation, decision engine, graph rendering."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from choregos_contracts import Workflow

from .engine import Decision, WorkflowEngine
from .flow import CONTEXT_DOCUMENTS, PULL_REQUEST_SECTIONS, artifact_flow
from .graph import to_graph, to_mermaid
from .parser import checksum, dump_workflow, parse_workflow, parse_workflow_file
from .process import RESUMES, to_process
from .validator import ValidationReport, validate_workflow

TEMPLATES_DIR = Path(__file__).parent / "templates"
#: The templates SHIPPED with the package. This is no longer the list of what is available:
#: a deployment can bring others (see `extra_templates_dirs`).
TEMPLATE_NAMES = ("default-simple", "full-auto", "advanced", "default-request", "blank", "decision-triage")


def extra_templates_dirs() -> list[Path]:
    """Templates brought by the DEPLOYMENT, separated by `:` in
    `CHOREGOS_WORKFLOW_TEMPLATES_DIR`.

    The same seam as the playbooks, for the same reason: a trade has its own chains of states,
    and they have no place in this package. Until 2026-09-26 the list was frozen at three names
    and `template_path` refused everything else; the only way out was to post the full YAML
    through `PUT /projects/{id}/workflow`, so without a name, without reuse, and without two
    projects being able to start from the same model.

    A deployment template wins over the package one: this is how a house adapts
    `default-simple` without rewriting the platform.
    """
    raw = os.environ.get("CHOREGOS_WORKFLOW_TEMPLATES_DIR", "")
    return [Path(p) for p in raw.split(":") if p.strip()]


def _directories() -> list[Path]:
    # The deployment FIRST: the first one found wins.
    return [*extra_templates_dirs(), TEMPLATES_DIR]


def template_names() -> tuple[str, ...]:
    """The templates really available, deployment ones included, without duplicates."""
    seen: dict[str, None] = {}
    for directory in _directories():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            seen.setdefault(path.stem, None)
    return tuple(seen)


def template_path(name: str) -> Path:
    for directory in _directories():
        path = directory / f"{name}.yaml"
        if path.exists():
            return path
    known = ", ".join(template_names()) or "none"
    raise FileNotFoundError(f"unknown workflow template: {name} (known: {known})")


@lru_cache(maxsize=8)
def load_template(name: str) -> Workflow:
    """Load a template shipped with the package or brought by the deployment."""
    workflow, _ = parse_workflow_file(template_path(name))
    return workflow


def forget_templates() -> None:
    """Empty the cache: a directory added after the first load must be seen."""
    load_template.cache_clear()


def template_yaml(name: str) -> str:
    return template_path(name).read_text(encoding="utf-8")


__all__ = [
    "CONTEXT_DOCUMENTS",
    "PULL_REQUEST_SECTIONS",
    "RESUMES",
    "TEMPLATES_DIR",
    "TEMPLATE_NAMES",
    "Decision",
    "ValidationReport",
    "WorkflowEngine",
    "artifact_flow",
    "checksum",
    "dump_workflow",
    "extra_templates_dirs",
    "forget_templates",
    "load_template",
    "parse_workflow",
    "parse_workflow_file",
    "template_names",
    "template_path",
    "template_yaml",
    "to_graph",
    "to_mermaid",
    "to_process",
    "validate_workflow",
]

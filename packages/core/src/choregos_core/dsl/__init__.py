"""DSL de workflow : chargement, validation, moteur de décision, rendu graphique."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from choregos_contracts import Workflow

from .engine import Decision, WorkflowEngine
from .graph import to_graph, to_mermaid
from .parser import checksum, dump_workflow, parse_workflow, parse_workflow_file
from .validator import ValidationReport, validate_workflow

TEMPLATES_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAMES = ("default-simple", "full-auto", "advanced")


def template_path(name: str) -> Path:
    path = TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"template de workflow inconnu : {name} (connus : {', '.join(TEMPLATE_NAMES)})"
        )
    return path


@lru_cache(maxsize=8)
def load_template(name: str) -> Workflow:
    """Charge un template livré (`default-simple`, `full-auto`, `advanced`)."""
    workflow, _ = parse_workflow_file(template_path(name))
    return workflow


def template_yaml(name: str) -> str:
    return template_path(name).read_text(encoding="utf-8")


__all__ = [
    "TEMPLATES_DIR",
    "TEMPLATE_NAMES",
    "Decision",
    "ValidationReport",
    "WorkflowEngine",
    "checksum",
    "dump_workflow",
    "load_template",
    "parse_workflow",
    "parse_workflow_file",
    "template_path",
    "template_yaml",
    "to_graph",
    "to_mermaid",
    "validate_workflow",
]

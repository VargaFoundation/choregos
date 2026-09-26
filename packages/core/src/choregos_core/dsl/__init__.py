"""DSL de workflow : chargement, validation, moteur de décision, rendu graphique."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from choregos_contracts import Workflow

from .engine import Decision, WorkflowEngine
from .graph import to_graph, to_mermaid
from .parser import checksum, dump_workflow, parse_workflow, parse_workflow_file
from .validator import ValidationReport, validate_workflow

TEMPLATES_DIR = Path(__file__).parent / "templates"
#: Les templates LIVRÉS avec le paquet. Ce n'est plus la liste de ce qui est disponible :
#: un déploiement peut en apporter d'autres (voir `extra_templates_dirs`).
TEMPLATE_NAMES = ("default-simple", "full-auto", "advanced")


def extra_templates_dirs() -> list[Path]:
    """Templates apportés par le DÉPLOIEMENT, séparés par `:` dans
    `CHOREGOS_WORKFLOW_TEMPLATES_DIR`.

    Même couture que les playbooks, et pour la même raison : un métier a ses propres
    enchaînements d'états, et ils n'ont rien à faire dans ce paquet. Jusqu'au 2026-09-26 la
    liste était figée à trois noms et `template_path` refusait tout le reste — la seule issue
    était de poster le YAML complet par `PUT /projects/{id}/workflow`, donc sans nom, sans
    réutilisation, et sans que deux projets puissent partir du même modèle.

    Un template du déploiement l'emporte sur celui du paquet : c'est ainsi qu'une maison
    adapte `default-simple` sans réécrire la plateforme.
    """
    brut = os.environ.get("CHOREGOS_WORKFLOW_TEMPLATES_DIR", "")
    return [Path(p) for p in brut.split(":") if p.strip()]


def _dossiers() -> list[Path]:
    # Le déploiement D'ABORD : le premier trouvé gagne.
    return [*extra_templates_dirs(), TEMPLATES_DIR]


def template_names() -> tuple[str, ...]:
    """Les templates réellement disponibles, déploiement compris, sans doublon."""
    vus: dict[str, None] = {}
    for dossier in _dossiers():
        if not dossier.is_dir():
            continue
        for chemin in sorted(dossier.glob("*.yaml")):
            vus.setdefault(chemin.stem, None)
    return tuple(vus)


def template_path(name: str) -> Path:
    for dossier in _dossiers():
        chemin = dossier / f"{name}.yaml"
        if chemin.exists():
            return chemin
    connus = ", ".join(template_names()) or "aucun"
    raise FileNotFoundError(f"template de workflow inconnu : {name} (connus : {connus})")


@lru_cache(maxsize=8)
def load_template(name: str) -> Workflow:
    """Charge un template livré ou apporté par le déploiement."""
    workflow, _ = parse_workflow_file(template_path(name))
    return workflow


def oublier_les_templates() -> None:
    """Vide le cache : un répertoire ajouté après le premier chargement doit être vu."""
    load_template.cache_clear()


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
    "extra_templates_dirs",
    "load_template",
    "oublier_les_templates",
    "parse_workflow",
    "parse_workflow_file",
    "template_names",
    "template_path",
    "template_yaml",
    "to_graph",
    "to_mermaid",
    "validate_workflow",
]

# SPDX-License-Identifier: Apache-2.0
"""Les gabarits de stack : où ils vivent, et ce que leur manifeste livre à un projet neuf.

Un gabarit est un dossier `templates/<nom>/` — livré avec la plateforme, ou désigné par
`CHOREGOS_TEMPLATES_DIR` — ou une ligne `templates` publiée par un administrateur, qui l'emporte.
Sous `defaults`, son manifeste dit les workflows d'un projet né de lui, le défaut et le routage
(ADR 0031), et la politique. `ensure_defaults` l'ignorait : tout projet recevait `default-simple`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from choregos_core import preset_yaml, template_yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Template
from ..errors import unprocessable

WORKFLOW_SANS_GABARIT = "template:default-simple@1"
POLITIQUE_SANS_GABARIT = "preset:solo"


def repertoire_des_gabarits() -> Path:
    """Le même dossier que l'orchestrateur, lu par le même réglage."""
    configure = os.environ.get("CHOREGOS_TEMPLATES_DIR", "").strip()
    return Path(configure) if configure else Path(__file__).resolve().parents[5] / "templates"


@dataclass(frozen=True)
class Livraison:
    """Ce qu'un gabarit livre à un projet neuf : des sources YAML, pas encore publiées."""

    workflows: list[str]
    politique: str
    defaut: str | None = None
    routage: list[dict[str, Any]] = field(default_factory=list)


async def manifeste_du_gabarit(
    session: AsyncSession, template_ref: str | None
) -> tuple[dict[str, Any], Path | None]:
    """Le manifeste de `nom[@version]`, et le dossier du gabarit quand il est sur disque.

    Un gabarit publié en base n'a pas de dossier : il ne peut livrer que des workflows du cœur.
    """
    if not template_ref:
        return {}, None
    nom, _, version = template_ref.partition("@")
    requete = select(Template).where(Template.name == nom)
    if version:
        requete = requete.where(Template.version == version)
    ligne = (
        await session.execute(requete.order_by(Template.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    if ligne is not None:
        return dict(ligne.manifest or {}), None
    dossier = repertoire_des_gabarits() / nom
    chemin = dossier / "manifest.yaml"
    if not chemin.is_file():
        raise unprocessable(f"gabarit `{template_ref}` introuvable")
    return dict(yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}), dossier


def livraison(manifeste: dict[str, Any], dossier: Path | None) -> Livraison:
    """Les sources que livre un manifeste ; sans gabarit, `default-simple` et `solo`."""
    defauts = manifeste.get("defaults") or {}
    refs = list(defauts.get("workflows") or ([defauts["workflow"]] if defauts.get("workflow") else []))
    return Livraison(
        workflows=[_source_du_workflow(ref, dossier) for ref in refs or [WORKFLOW_SANS_GABARIT]],
        politique=_source_de_la_politique(str(defauts.get("policy") or POLITIQUE_SANS_GABARIT)),
        defaut=defauts.get("default_workflow"),
        routage=list(defauts.get("routing") or []),
    )


def _source_du_workflow(ref: str, dossier: Path | None) -> str:
    if ref.startswith("template:"):
        nom = ref.removeprefix("template:").partition("@")[0]
        try:
            return template_yaml(nom)
        except FileNotFoundError as erreur:
            raise unprocessable(f"le gabarit livre `{ref}`, que la plateforme ne connaît pas") from erreur
    if dossier is None:
        raise unprocessable(
            f"`{ref}` : un gabarit publié en base ne porte aucun fichier ; nommez `template:<nom>@<v>`"
        )
    racine = dossier.resolve()
    chemin = (racine / ref).resolve()
    if not chemin.is_relative_to(racine):
        raise unprocessable(f"`{ref}` sort du dossier du gabarit")
    if not chemin.is_file():
        raise unprocessable(f"`{ref}` : fichier absent du gabarit")
    return chemin.read_text(encoding="utf-8")


def _source_de_la_politique(ref: str) -> str:
    nom = ref.removeprefix("preset:")
    if nom == ref or "/" in nom or nom.startswith("."):
        raise unprocessable(f"politique `{ref}` : seule la forme `preset:<nom>` est livrable par un gabarit")
    try:
        return preset_yaml(nom)
    except FileNotFoundError as erreur:
        raise unprocessable(
            f"le gabarit livre la politique `{ref}`, que la plateforme ne connaît pas"
        ) from erreur


__all__ = ["Livraison", "livraison", "manifeste_du_gabarit", "repertoire_des_gabarits"]

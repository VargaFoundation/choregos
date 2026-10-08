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
    #: Les agents (leur document `AgentCreate`) et les skills (leurs fichiers) qu'il installe dans
    #: l'organisation (S20-07).
    agents: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, str]] = field(default_factory=list)
    #: Ce que des greffons installent (S20-09) : nom de l'installateur → fichiers de son dossier.
    extensions: dict[str, dict[str, str]] = field(default_factory=dict)


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
        raise unprocessable(f"template `{template_ref}` not found")
    return dict(yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}), dossier


def livraison(manifeste: dict[str, Any], dossier: Path | None) -> Livraison:
    """Les sources que livre un manifeste ; sans gabarit, `default-simple` et `solo`."""
    defauts = manifeste.get("defaults") or {}
    refs = list(defauts.get("workflows") or ([defauts["workflow"]] if defauts.get("workflow") else []))
    return Livraison(
        workflows=[_source_du_workflow(ref, dossier) for ref in refs or [WORKFLOW_SANS_GABARIT]],
        politique=_source_de_la_politique(str(defauts.get("policy") or POLITIQUE_SANS_GABARIT), dossier),
        defaut=defauts.get("default_workflow"),
        routage=list(defauts.get("routing") or []),
        agents=[_document_de_l_agent(str(ref), dossier) for ref in defauts.get("agents") or []],
        skills=[_fichiers_de_la_skill(str(ref), dossier) for ref in defauts.get("skills") or []],
        extensions={
            str(nom): _fichiers_du_dossier(str(ref), dossier, f"extension `{nom}`")
            for nom, ref in (defauts.get("extensions") or {}).items()
        },
    )


def _chemin_du_gabarit(ref: str, dossier: Path | None) -> Path:
    """Un chemin que nomme le manifeste : dans le dossier du gabarit, jamais au-dehors."""
    if dossier is None:
        raise unprocessable(f"`{ref}`: a template published in the database carries no file")
    racine = dossier.resolve()
    chemin = (racine / ref).resolve()
    if not chemin.is_relative_to(racine):
        raise unprocessable(f"`{ref}` goes outside the template's folder")
    return chemin


#: Un gabarit nomme un agent ou une skill du catalogue de la plateforme par `catalogue:<nom>` (ADR 0040).
PREFIXE_CATALOGUE = "catalogue:"


def _document_de_l_agent(ref: str, dossier: Path | None) -> dict[str, Any]:
    if ref.startswith(PREFIXE_CATALOGUE):
        from choregos_core.catalogue_d_agents import entree

        trouve = entree(ref.removeprefix(PREFIXE_CATALOGUE))
        if trouve is None:
            raise unprocessable(f"`{ref}`: the platform's catalogue has no such agent")
        return trouve.document
    chemin = _chemin_du_gabarit(ref, dossier)
    if not chemin.is_file():
        raise unprocessable(f"`{ref}`: agent missing from the template")
    document = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise unprocessable(f"`{ref}`: an agent is described by an object (`slug`, `display_name`, `spec`)")
    return document


def _fichiers_de_la_skill(ref: str, dossier: Path | None) -> dict[str, str]:
    if ref.startswith(PREFIXE_CATALOGUE):
        from choregos_core.catalogue_d_agents import fichiers_de_la_skill

        try:
            return fichiers_de_la_skill(ref.removeprefix(PREFIXE_CATALOGUE))
        except KeyError as erreur:
            raise unprocessable(f"`{ref}`: the platform's catalogue has no such skill") from erreur
    return _fichiers_du_dossier(ref, dossier, "skill")


def _fichiers_du_dossier(ref: str, dossier: Path | None, quoi: str) -> dict[str, str]:
    """Les fichiers d'un dossier du gabarit, chemin relatif → texte ; un lien symbolique est refusé,
    comme dans une archive (rien ne doit se lire hors du dossier)."""
    racine = _chemin_du_gabarit(ref, dossier)
    if not racine.is_dir():
        raise unprocessable(f"`{ref}`: {quoi} missing from the template")
    fichiers: dict[str, str] = {}
    for chemin in sorted(racine.rglob("*")):
        if chemin.is_symlink():
            raise unprocessable(f"`{ref}`: symbolic link refused ({chemin.name})")
        if chemin.is_file():
            fichiers[chemin.relative_to(racine).as_posix()] = chemin.read_text(encoding="utf-8")
    return fichiers


def _source_du_workflow(ref: str, dossier: Path | None) -> str:
    if ref.startswith("template:"):
        nom = ref.removeprefix("template:").partition("@")[0]
        try:
            return template_yaml(nom)
        except FileNotFoundError as erreur:
            raise unprocessable(f"the template ships `{ref}`, which the platform does not know") from erreur
    if dossier is None:
        raise unprocessable(
            f"`{ref}`: a template published in the database carries no file; name `template:<name>@<v>`"
        )
    chemin = _chemin_du_gabarit(ref, dossier)
    if not chemin.is_file():
        raise unprocessable(f"`{ref}`: file missing from the template")
    return chemin.read_text(encoding="utf-8")


def _source_de_la_politique(ref: str, dossier: Path | None) -> str:
    """`preset:<nom>`, ou un fichier du gabarit (`./policy.yaml`) : un gabarit de livraison logicielle
    a ses budgets et ses trains, qu'aucun preset ne porte (S21-22). Un chemin reste dans le dossier."""
    if not ref.startswith("preset:"):
        chemin = _chemin_du_gabarit(ref, dossier)
        if not chemin.is_file():
            raise unprocessable(f"policy `{ref}`: file missing from the template")
        return chemin.read_text(encoding="utf-8")
    nom = ref.removeprefix("preset:")
    if "/" in nom or nom.startswith("."):
        raise unprocessable(f"policy `{ref}`: a preset is named, not looked up in a path")
    try:
        return preset_yaml(nom)
    except FileNotFoundError as erreur:
        raise unprocessable(
            f"the template ships policy `{ref}`, which the platform does not know"
        ) from erreur


__all__ = ["Livraison", "livraison", "manifeste_du_gabarit", "repertoire_des_gabarits"]

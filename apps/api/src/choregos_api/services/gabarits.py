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
from choregos_core.paths import PathOutsideError, files_under, inside, symlinks_under
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Template
from ..errors import unprocessable

WORKFLOW_SANS_GABARIT = "template:default-simple@1"
#: Without a template and without a repository: a workflow that delivers no code (ADR 0107).
WORKFLOW_WITHOUT_REPOSITORY = "template:default-request@1"
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
    #: Among these agents, those the template takes from the catalogue: slug -> `catalogue:<slug>@<version>`,
    #: the provenance of a direct install (S39-25).
    agents_du_catalogue: dict[str, str] = field(default_factory=dict)
    #: Ce que des greffons installent (S20-09) : nom de l'installateur → fichiers de son dossier.
    extensions: dict[str, dict[str, str]] = field(default_factory=dict)


async def manifeste_du_gabarit(
    session: AsyncSession, template_ref: str | None, org_id: str | None = None
) -> tuple[dict[str, Any], Path | None]:
    """Le manifeste de `nom[@version]`, et le dossier du gabarit quand il est sur disque.

    Un gabarit publié en base n'a pas de dossier : il ne peut livrer que des workflows du cœur.
    `plugin:<plugin>/<template>` names a template an installed plugin of `org_id` brought: it carries its
    files (ADR 0093, S43-04).
    """
    if not template_ref:
        return {}, None
    if template_ref.startswith("plugin:"):
        from .plugin_installer import plugin_template

        manifest, folder = await plugin_template(session, org_id, template_ref)
        return manifest, folder
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


def livraison(manifeste: dict[str, Any], dossier: Path | None, *, has_repository: bool = True) -> Livraison:
    """The sources a manifest ships; without a template, `solo` and `default-simple` — or
    `default-request` for a project that names no repository (ADR 0107). A template's own workflows
    always win."""
    defauts = manifeste.get("defaults") or {}
    refs = list(defauts.get("workflows") or ([defauts["workflow"]] if defauts.get("workflow") else []))
    fallback = WORKFLOW_SANS_GABARIT if has_repository else WORKFLOW_WITHOUT_REPOSITORY
    return Livraison(
        workflows=[_source_du_workflow(ref, dossier) for ref in refs or [fallback]],
        politique=_source_de_la_politique(str(defauts.get("policy") or POLITIQUE_SANS_GABARIT), dossier),
        defaut=defauts.get("default_workflow"),
        routage=list(defauts.get("routing") or []),
        agents=[_document_de_l_agent(str(ref), dossier) for ref in defauts.get("agents") or []],
        skills=[_fichiers_de_la_skill(str(ref), dossier) for ref in defauts.get("skills") or []],
        agents_du_catalogue=_agents_du_catalogue(defauts.get("agents") or []),
        extensions={
            str(nom): _fichiers_du_dossier(str(ref), dossier, f"extension `{nom}`")
            for nom, ref in (defauts.get("extensions") or {}).items()
        },
    )


def shipped_workflows(manifeste: dict[str, Any], dossier: Path | None) -> list[tuple[str, str]]:
    """`(ref, YAML source)` of each workflow the manifest ships today, in its order (S38-20).

    The same sources `livraison` gives a new project, with the reference each comes from, so that a
    project born of this template can be told which file to publish again."""
    defauts = manifeste.get("defaults") or {}
    refs = list(defauts.get("workflows") or ([defauts["workflow"]] if defauts.get("workflow") else []))
    return [(str(ref), _source_du_workflow(str(ref), dossier)) for ref in refs or [WORKFLOW_SANS_GABARIT]]


def _chemin_du_gabarit(ref: str, dossier: Path | None) -> Path:
    """Un chemin que nomme le manifeste : dans le dossier du gabarit, jamais au-dehors."""
    if dossier is None:
        raise unprocessable(f"`{ref}`: a template published in the database carries no file")
    try:
        return inside(dossier, ref)
    except PathOutsideError as erreur:
        raise unprocessable(f"`{ref}` goes outside the template's folder") from erreur


#: Un gabarit nomme un agent ou une skill du catalogue de la plateforme par `catalogue:<nom>` (ADR 0040).
#: The catalogue is the plugins' since S43-09 (ADR 0093 §6): the name resolves through them.
PREFIXE_CATALOGUE = "catalogue:"


def _agents_du_catalogue(refs: list[Any]) -> dict[str, str]:
    """slug -> `plugin:<plugin>/<slug>@<version>`: the provenance a direct install would write."""
    from .catalogue_d_agents import offline_catalogue

    view = offline_catalogue()
    provenances: dict[str, str] = {}
    for ref in map(str, refs):
        trouve = (
            view.entry(ref.removeprefix(PREFIXE_CATALOGUE)) if ref.startswith(PREFIXE_CATALOGUE) else None
        )
        if trouve is not None:
            provenances[trouve.slug] = trouve.provenance
    return provenances


def _document_de_l_agent(ref: str, dossier: Path | None) -> dict[str, Any]:
    if ref.startswith(PREFIXE_CATALOGUE):
        from .catalogue_d_agents import offline_catalogue

        trouve = offline_catalogue().entry(ref.removeprefix(PREFIXE_CATALOGUE))
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
        from .catalogue_d_agents import offline_catalogue

        skill = offline_catalogue().skills.get(ref.removeprefix(PREFIXE_CATALOGUE))
        if skill is None:
            raise unprocessable(f"`{ref}`: the platform's catalogue has no such skill")
        return dict(skill.files)
    return _fichiers_du_dossier(ref, dossier, "skill")


def _fichiers_du_dossier(ref: str, dossier: Path | None, quoi: str) -> dict[str, str]:
    """Les fichiers d'un dossier du gabarit, chemin relatif → texte ; un lien symbolique est refusé,
    comme dans une archive (rien ne doit se lire hors du dossier)."""
    racine = _chemin_du_gabarit(ref, dossier)
    if not racine.is_dir():
        raise unprocessable(f"`{ref}`: {quoi} missing from the template")
    liens = symlinks_under(racine)
    if liens:
        raise unprocessable(f"`{ref}`: symbolic link refused ({Path(liens[0]).name})")
    return {nom: chemin.read_text(encoding="utf-8") for nom, chemin in files_under(racine).items()}


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


__all__ = ["Livraison", "livraison", "manifeste_du_gabarit", "repertoire_des_gabarits", "shipped_workflows"]

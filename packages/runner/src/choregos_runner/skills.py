# SPDX-License-Identifier: Apache-2.0
"""Les skills de l'agent, posées là où son backend les lit (ADR 0033).

Le `StageInput` nomme chaque skill par son nom, sa version et son **empreinte**, posés quand le
run a été préparé. Les fichiers viennent de l'API interne ; le runner recalcule leur empreinte
avant d'écrire quoi que ce soit, et une empreinte fausse arrête le run : une skill modifiée entre
la préparation et l'exécution n'est pas celle que la version de l'agent portait.

Un backend qui ne lit pas de skills (`skills_dir = None`) les reçoit sous `.choregos/skills`, avec
un index dans son prompt. Dans tous les cas, ces fichiers restent hors du diff.
"""

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Any

import yaml
from choregos_contracts import SkillRef, empreinte_de_skill

REPLI = ".choregos/skills"


class SkillInvalide(RuntimeError):  # noqa: N818 - un refus motivé, qui arrête le run
    pass


def _description(skill_md: str) -> str:
    if not skill_md.startswith("---"):
        return ""
    fin = skill_md.find("\n---", 3)
    try:
        entete = yaml.safe_load(skill_md[3:fin]) if fin > 0 else {}
    except yaml.YAMLError:
        return ""
    return str((entete or {}).get("description") or "").strip() if isinstance(entete, dict) else ""


def poser(
    livrees: list[dict[str, Any]], attendues: list[SkillRef], workspace: Path, skills_dir: str | None
) -> tuple[list[str], str]:
    """Vérifie et écrit les skills ; rend les fichiers écrits (hors du diff) et l'index du prompt."""
    par_nom = {str(skill.get("slug")): skill for skill in livrees}
    racine = skills_dir or REPLI
    ecrits: list[str] = []
    index: list[str] = []
    for ref in attendues:
        skill = par_nom.get(ref.slug)
        if skill is None:
            raise SkillInvalide(f"the skill `{ref.slug}@{ref.version}` was not delivered")
        fichiers: dict[str, str] = dict(skill.get("files") or {})
        if empreinte_de_skill(fichiers) != ref.digest:
            raise SkillInvalide(
                f"the skill `{ref.slug}@{ref.version}` does not have the digest the run expects"
            )
        for chemin, texte in fichiers.items():
            normalise = posixpath.normpath(chemin)
            if normalise.startswith(("../", "/")) or normalise == "..":
                raise SkillInvalide(f"the skill `{ref.slug}` reaches outside its folder: `{chemin}`")
            relatif = f"{racine}/{ref.slug}/{normalise}"
            cible = workspace / relatif
            cible.parent.mkdir(parents=True, exist_ok=True)
            cible.write_text(texte, encoding="utf-8")
            ecrits.append(relatif)
        if skills_dir is None:
            description = _description(fichiers.get("SKILL.md", ""))
            index.append(f"- `{racine}/{ref.slug}/SKILL.md`" + (f" — {description}" if description else ""))
    texte_index = (
        "## Skills\n\nBefore you act, read the skill that fits the task:\n\n" + "\n".join(index)
        if index
        else ""
    )
    return ecrits, texte_index


__all__ = ["REPLI", "SkillInvalide", "poser"]

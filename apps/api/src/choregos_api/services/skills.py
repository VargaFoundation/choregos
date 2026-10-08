# SPDX-License-Identifier: Apache-2.0
"""Une skill : un dossier — `SKILL.md` et ses fichiers — qu'un agent porte (ADR 0033).

Ce qui entre est vérifié ICI, une fois, avant d'être une version :

- `SKILL.md` à la racine, son en-tête YAML avec un `name` — celui de la skill, sinon le dossier et
  la bibliothèque se contrediraient — et une `description` ;
- **aucune permission déclarée** : un champ `allowed-tools` est refusé. Ce qu'un agent peut appeler
  se décide par sa version et son projet, jamais par un fichier qu'il lit ;
- une archive ne sort pas de son dossier (zip-slip), ne porte pas de lien symbolique, tient en 64
  fichiers et 512 Kio, et ne contient que du texte.
"""

from __future__ import annotations

import io
import posixpath
import re
import stat
import zipfile
from dataclasses import dataclass

import yaml
from choregos_contracts import empreinte_de_skill

MAX_FICHIERS = 64
MAX_OCTETS = 512 * 1024
NOM = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class SkillRefusee(ValueError):  # noqa: N818 - un refus motivé, rendu en 422
    pass


@dataclass(frozen=True)
class Entete:
    name: str
    description: str


def chemin_sur(chemin: str) -> str:
    """Le chemin normalisé d'un fichier de la skill, ou un refus s'il sort de son dossier."""
    if not chemin or "\\" in chemin or chemin.startswith("/") or re.match(r"^[A-Za-z]:", chemin):
        raise SkillRefusee(f"`{chemin}`: a relative path with `/` separators is expected")
    normalise = posixpath.normpath(chemin)
    if normalise == ".." or normalise.startswith("../") or "/../" in f"/{normalise}/":
        raise SkillRefusee(f"`{chemin}` goes outside the skill's folder")
    return normalise


def lire_le_zip(contenu: bytes) -> dict[str, str]:
    """Les fichiers d'une archive, vérifiés ; un dossier racine commun est retiré."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile as erreur:
        raise SkillRefusee("this is not a zip archive") from erreur
    with archive:
        entrees = [info for info in archive.infolist() if not info.is_dir()]
        if len(entrees) > MAX_FICHIERS:
            raise SkillRefusee(f"{len(entrees)} files: {MAX_FICHIERS} at most")
        if sum(info.file_size for info in entrees) > MAX_OCTETS:
            raise SkillRefusee(f"more than {MAX_OCTETS // 1024} KiB once uncompressed")
        fichiers: dict[str, str] = {}
        lus = 0
        for info in entrees:
            if stat.S_ISLNK(info.external_attr >> 16):
                raise SkillRefusee(f"`{info.filename}` is a symbolic link: refused")
            chemin = chemin_sur(info.filename)
            donnees = archive.read(info)
            lus += len(donnees)
            if lus > MAX_OCTETS:
                raise SkillRefusee(f"more than {MAX_OCTETS // 1024} KiB once uncompressed")
            try:
                fichiers[chemin] = donnees.decode("utf-8")
            except UnicodeDecodeError as erreur:
                raise SkillRefusee(f"`{chemin}` is not UTF-8 text") from erreur
    racines = {chemin.split("/", 1)[0] for chemin in fichiers}
    if len(racines) == 1 and "SKILL.md" not in fichiers and all("/" in chemin for chemin in fichiers):
        (racine,) = racines
        fichiers = {chemin[len(racine) + 1 :]: texte for chemin, texte in fichiers.items()}
    return fichiers


def entete(skill_md: str) -> dict[str, object]:
    if not skill_md.startswith("---"):
        raise SkillRefusee("SKILL.md must start with its YAML front matter (`---`)")
    fin = skill_md.find("\n---", 3)
    if fin < 0:
        raise SkillRefusee("the YAML front matter of SKILL.md is not closed (`---`)")
    try:
        valeurs = yaml.safe_load(skill_md[3:fin]) or {}
    except yaml.YAMLError as erreur:
        raise SkillRefusee(f"the front matter of SKILL.md is not YAML: {erreur}") from erreur
    if not isinstance(valeurs, dict):
        raise SkillRefusee("the front matter of SKILL.md must be an object")
    return valeurs


def valider(fichiers: dict[str, str], slug: str | None = None) -> Entete:
    """Ce qu'une version de skill doit tenir ; rend son nom et sa description."""
    if not fichiers:
        raise SkillRefusee("a skill without any file")
    if len(fichiers) > MAX_FICHIERS:
        raise SkillRefusee(f"{len(fichiers)} files: {MAX_FICHIERS} at most")
    for chemin in fichiers:
        if chemin_sur(chemin) != chemin:
            raise SkillRefusee(f"`{chemin}`: path is not normalised")
    if sum(len(texte.encode("utf-8")) for texte in fichiers.values()) > MAX_OCTETS:
        raise SkillRefusee(f"more than {MAX_OCTETS // 1024} KiB")
    if "SKILL.md" not in fichiers:
        raise SkillRefusee("SKILL.md is missing at the root")
    valeurs = entete(fichiers["SKILL.md"])
    if "allowed-tools" in valeurs or "allowed_tools" in valeurs:
        raise SkillRefusee(
            "a skill declares no permission (`allowed-tools`): what an agent may call is decided by "
            "its version and its project"
        )
    nom, description = valeurs.get("name"), valeurs.get("description")
    if not isinstance(nom, str) or not NOM.match(nom):
        raise SkillRefusee("`name` is missing, or is not a skill name (lowercase letters, digits, `-`)")
    if slug is not None and nom != slug:
        raise SkillRefusee(f"SKILL.md is named `{nom}`, the skill `{slug}`: the two must match")
    if not isinstance(description, str) or not description.strip():
        raise SkillRefusee("`description` is missing: it is what an agent reads to choose the skill")
    return Entete(name=nom, description=description.strip())


def empreinte(fichiers: dict[str, str]) -> str:
    """L'empreinte d'une version : le runner la recalcule avant de poser les fichiers."""
    return empreinte_de_skill(fichiers)

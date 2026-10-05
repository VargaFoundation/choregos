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
        raise SkillRefusee(f"`{chemin}` : un chemin relatif, au séparateur `/`, est attendu")
    normalise = posixpath.normpath(chemin)
    if normalise == ".." or normalise.startswith("../") or "/../" in f"/{normalise}/":
        raise SkillRefusee(f"`{chemin}` sort du dossier de la skill")
    return normalise


def lire_le_zip(contenu: bytes) -> dict[str, str]:
    """Les fichiers d'une archive, vérifiés ; un dossier racine commun est retiré."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile as erreur:
        raise SkillRefusee("ce n'est pas une archive zip") from erreur
    with archive:
        entrees = [info for info in archive.infolist() if not info.is_dir()]
        if len(entrees) > MAX_FICHIERS:
            raise SkillRefusee(f"{len(entrees)} fichiers : {MAX_FICHIERS} au plus")
        if sum(info.file_size for info in entrees) > MAX_OCTETS:
            raise SkillRefusee(f"plus de {MAX_OCTETS // 1024} Kio une fois décompressée")
        fichiers: dict[str, str] = {}
        lus = 0
        for info in entrees:
            if stat.S_ISLNK(info.external_attr >> 16):
                raise SkillRefusee(f"`{info.filename}` est un lien symbolique : refusé")
            chemin = chemin_sur(info.filename)
            donnees = archive.read(info)
            lus += len(donnees)
            if lus > MAX_OCTETS:
                raise SkillRefusee(f"plus de {MAX_OCTETS // 1024} Kio une fois décompressée")
            try:
                fichiers[chemin] = donnees.decode("utf-8")
            except UnicodeDecodeError as erreur:
                raise SkillRefusee(f"`{chemin}` n'est pas du texte UTF-8") from erreur
    racines = {chemin.split("/", 1)[0] for chemin in fichiers}
    if len(racines) == 1 and "SKILL.md" not in fichiers and all("/" in chemin for chemin in fichiers):
        (racine,) = racines
        fichiers = {chemin[len(racine) + 1 :]: texte for chemin, texte in fichiers.items()}
    return fichiers


def entete(skill_md: str) -> dict[str, object]:
    if not skill_md.startswith("---"):
        raise SkillRefusee("SKILL.md doit commencer par son en-tête YAML (`---`)")
    fin = skill_md.find("\n---", 3)
    if fin < 0:
        raise SkillRefusee("l'en-tête YAML de SKILL.md n'est pas fermé (`---`)")
    try:
        valeurs = yaml.safe_load(skill_md[3:fin]) or {}
    except yaml.YAMLError as erreur:
        raise SkillRefusee(f"l'en-tête de SKILL.md n'est pas du YAML : {erreur}") from erreur
    if not isinstance(valeurs, dict):
        raise SkillRefusee("l'en-tête de SKILL.md doit être un objet")
    return valeurs


def valider(fichiers: dict[str, str], slug: str | None = None) -> Entete:
    """Ce qu'une version de skill doit tenir ; rend son nom et sa description."""
    if not fichiers:
        raise SkillRefusee("une skill sans fichier")
    if len(fichiers) > MAX_FICHIERS:
        raise SkillRefusee(f"{len(fichiers)} fichiers : {MAX_FICHIERS} au plus")
    for chemin in fichiers:
        if chemin_sur(chemin) != chemin:
            raise SkillRefusee(f"`{chemin}` : chemin non normalisé")
    if sum(len(texte.encode("utf-8")) for texte in fichiers.values()) > MAX_OCTETS:
        raise SkillRefusee(f"plus de {MAX_OCTETS // 1024} Kio")
    if "SKILL.md" not in fichiers:
        raise SkillRefusee("SKILL.md manque à la racine")
    valeurs = entete(fichiers["SKILL.md"])
    if "allowed-tools" in valeurs or "allowed_tools" in valeurs:
        raise SkillRefusee(
            "une skill ne déclare aucune permission (`allowed-tools`) : ce qu'un agent peut appeler se "
            "décide par sa version et son projet"
        )
    nom, description = valeurs.get("name"), valeurs.get("description")
    if not isinstance(nom, str) or not NOM.match(nom):
        raise SkillRefusee("`name` manque, ou n'est pas un nom de skill (minuscules, chiffres, `-`)")
    if slug is not None and nom != slug:
        raise SkillRefusee(f"SKILL.md s'appelle `{nom}`, la skill `{slug}` : les deux doivent coïncider")
    if not isinstance(description, str) or not description.strip():
        raise SkillRefusee("`description` manque : c'est ce qu'un agent lit pour choisir la skill")
    return Entete(name=nom, description=description.strip())


def empreinte(fichiers: dict[str, str]) -> str:
    """L'empreinte d'une version : le runner la recalcule avant de poser les fichiers."""
    return empreinte_de_skill(fichiers)

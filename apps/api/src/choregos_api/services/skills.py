# SPDX-License-Identifier: Apache-2.0
"""Une skill : un dossier — `SKILL.md` et ses fichiers — qu'un agent porte (ADR 0033).

Ce qui entre est vérifié ICI, une fois, avant d'être une version :

- `SKILL.md` à la racine, son en-tête YAML avec un `name` — celui de la skill, sinon le dossier et
  la bibliothèque se contrediraient — et une `description` ;
- **aucune permission déclarée** : un champ `allowed-tools` est refusé. Ce qu'un agent peut appeler
  se décide par sa version et son projet, jamais par un fichier qu'il lit ;
- an archive does not leave its folder (zip-slip), carries no symbolic link, fits in 256 files and
  5 MiB (S32-06), and holds only text, or an inert binary (a font, an image: base64, magic bytes
  checked, 512 KiB at most, `skill_files`).
"""

from __future__ import annotations

import io
import posixpath
import re
import stat
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml
from choregos_contracts import (
    check_inert_binary,
    empreinte_de_skill,
    encode_binary,
    is_inert_binary_path,
    skill_file_bytes,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ..db.models import Organization, Skill, SkillVersion
    from ..deps import Me

MAX_FICHIERS = 256
MAX_OCTETS = 5 * 1024 * 1024
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


def file_value(chemin: str, donnees: bytes) -> str:
    """The stored string of one imported file: base64 for an inert binary (checked), UTF-8 text
    for anything else; the reason it is refused otherwise."""
    if is_inert_binary_path(chemin):
        try:
            check_inert_binary(chemin, donnees)
        except ValueError as erreur:
            raise SkillRefusee(str(erreur)) from erreur
        return encode_binary(donnees)
    try:
        return donnees.decode("utf-8")
    except UnicodeDecodeError as erreur:
        raise SkillRefusee(f"`{chemin}` is not UTF-8 text, nor an accepted font or image") from erreur


def _size(chemin: str, valeur: str) -> int:
    """The decoded size of a stored file, which is what the caps count."""
    try:
        return len(skill_file_bytes(chemin, valeur))
    except ValueError as erreur:
        raise SkillRefusee(str(erreur)) from erreur


def lire_le_zip(contenu: bytes) -> dict[str, str]:
    """Les fichiers d'une archive, vérifiés ; un dossier racine commun est retiré."""
    return read_zip(contenu)[0]


def read_zip(contenu: bytes) -> tuple[dict[str, str], list[str]]:
    """The files of an archive, checked, and those carrying an executable bit (the gate reads them);
    a common root folder is dropped."""
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
        executables: set[str] = set()
        lus = 0
        for info in entrees:
            if stat.S_ISLNK(info.external_attr >> 16):
                raise SkillRefusee(f"`{info.filename}` is a symbolic link: refused")
            chemin = chemin_sur(info.filename)
            donnees = archive.read(info)
            lus += len(donnees)
            if lus > MAX_OCTETS:
                raise SkillRefusee(f"more than {MAX_OCTETS // 1024} KiB once uncompressed")
            fichiers[chemin] = file_value(chemin, donnees)
            if (info.external_attr >> 16) & 0o111:
                executables.add(chemin)
    racines = {chemin.split("/", 1)[0] for chemin in fichiers}
    if len(racines) == 1 and "SKILL.md" not in fichiers and all("/" in chemin for chemin in fichiers):
        (racine,) = racines
        fichiers = {chemin[len(racine) + 1 :]: texte for chemin, texte in fichiers.items()}
        executables = {chemin[len(racine) + 1 :] for chemin in executables}
    return fichiers, sorted(executables)


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
    for chemin, valeur in fichiers.items():
        if is_inert_binary_path(chemin):
            try:
                check_inert_binary(chemin, skill_file_bytes(chemin, valeur))
            except ValueError as erreur:
                raise SkillRefusee(str(erreur)) from erreur
    if sum(_size(chemin, valeur) for chemin, valeur in fichiers.items()) > MAX_OCTETS:
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


async def publish_version(  # noqa: PLR0913 - the old `_publier`, moved, plus one switch
    session: AsyncSession,
    principal: Me,
    organisation: Organization,
    fichiers: dict[str, str],
    slug: str | None,
    source: dict[str, object] | None = None,
    *,
    executables: list[str] | None = None,
    repo_licences: dict[str, str] | None = None,
    review_as_platform: bool = False,
) -> tuple[Skill, SkillVersion]:
    """Creates the skill (version 1) or publishes its next version, never a rewrite. The version
    goes through the gate (S32-06): its risk, what the scan finds; `high` waits for a second reviewer.

    `review_as_platform` is for a version the platform itself ships (the bundled skills of the catalogue,
    ADR 0065): it is approved by `platform` like the agent catalogue's skills (ADR 0050 §7), and only if
    the gate does not call it high risk — a high-risk version is never approved on the platform's word."""
    from ..audit import record
    from ..db.models import Skill, SkillVersion
    from ..errors import unprocessable
    from . import skill_gate

    try:
        tete = valider(fichiers, slug)
    except SkillRefusee as refus:
        raise unprocessable(str(refus)) from refus
    skill = (
        await session.execute(select(Skill).where(Skill.org_id == organisation.id, Skill.slug == tete.name))
    ).scalar_one_or_none()
    assessment = skill_gate.assess(fichiers, executables=executables or [], repo_licences=repo_licences)
    columns = skill_gate.columns(assessment)
    if review_as_platform:
        if assessment.risk == "high":
            raise unprocessable(
                f"`{tete.name}` is assessed high risk: the platform does not approve it on its own word"
            )
        columns = {**columns, "review_status": "approved", "reviewed_by": skill_gate.PLATFORM}
    if skill is None:
        skill = Skill(org_id=organisation.id, slug=tete.name, description=tete.description, status="active")
        session.add(skill)
        await session.flush()
    skill.description = tete.description
    derniere = (
        await session.execute(select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill.id))
    ).scalar() or 0
    version = SkillVersion(
        skill_id=skill.id,
        org_id=organisation.id,
        version=derniere + 1,
        files=dict(fichiers),
        digest=empreinte(fichiers),
        created_by=principal.email,
        source=source,
        **columns,
    )
    session.add(version)
    await session.flush()
    await record(
        session,
        principal,
        "skill.publish",
        org_id=organisation.id,
        target_type="skill",
        target_id=skill.id,
        version=version.version,
        risk=version.risk,
        review_status=version.review_status,
        **({"source": source} if source else {}),
    )
    return skill, version

# SPDX-License-Identifier: Apache-2.0
"""A skill: a folder (`SKILL.md` and its files) that an agent carries (ADR 0033).

What comes in is checked HERE, once, before it becomes a version:

- `SKILL.md` at the root, its YAML header with a `name` (the skill's own, otherwise the folder and
  the library would contradict each other) and a `description`;
- **no declared permission**: an `allowed-tools` field is refused. What an agent may call is
  decided by its version and its project, never by a file it reads;
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
from choregos_core.compat import alias_names
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ..db.models import Organization, Skill, SkillVersion
    from ..deps import Me

MAX_FILES = 256
MAX_BYTES = 5 * 1024 * 1024
NAME = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class SkillRefused(ValueError):  # noqa: N818 - a reasoned refusal, rendered as 422
    pass


@dataclass(frozen=True)
class FrontMatter:
    name: str
    description: str


def safe_path(path: str) -> str:
    """The normalised path of a skill file, or a refusal if it leaves the skill's folder."""
    if not path or "\\" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise SkillRefused(f"`{path}`: a relative path with `/` separators is expected")
    normalised = posixpath.normpath(path)
    if normalised == ".." or normalised.startswith("../") or "/../" in f"/{normalised}/":
        raise SkillRefused(f"`{path}` goes outside the skill's folder")
    return normalised


def file_value(path: str, data: bytes) -> str:
    """The stored string of one imported file: base64 for an inert binary (checked), UTF-8 text
    for anything else; the reason it is refused otherwise."""
    if is_inert_binary_path(path):
        try:
            check_inert_binary(path, data)
        except ValueError as error:
            raise SkillRefused(str(error)) from error
        return encode_binary(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillRefused(f"`{path}` is not UTF-8 text, nor an accepted font or image") from error


def _size(path: str, value: str) -> int:
    """The decoded size of a stored file, which is what the caps count."""
    try:
        return len(skill_file_bytes(path, value))
    except ValueError as error:
        raise SkillRefused(str(error)) from error


def read_zip_files(content: bytes) -> dict[str, str]:
    """The files of an archive, checked; a common root folder is dropped."""
    return read_zip(content)[0]


def read_zip(content: bytes) -> tuple[dict[str, str], list[str]]:
    """The files of an archive, checked, and those carrying an executable bit (the gate reads them);
    a common root folder is dropped."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        raise SkillRefused("this is not a zip archive") from error
    with archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        if len(entries) > MAX_FILES:
            raise SkillRefused(f"{len(entries)} files: {MAX_FILES} at most")
        if sum(info.file_size for info in entries) > MAX_BYTES:
            raise SkillRefused(f"more than {MAX_BYTES // 1024} KiB once uncompressed")
        files: dict[str, str] = {}
        executables: set[str] = set()
        total = 0
        for info in entries:
            if stat.S_ISLNK(info.external_attr >> 16):
                raise SkillRefused(f"`{info.filename}` is a symbolic link: refused")
            path = safe_path(info.filename)
            data = archive.read(info)
            total += len(data)
            if total > MAX_BYTES:
                raise SkillRefused(f"more than {MAX_BYTES // 1024} KiB once uncompressed")
            files[path] = file_value(path, data)
            if (info.external_attr >> 16) & 0o111:
                executables.add(path)
    roots = {path.split("/", 1)[0] for path in files}
    if len(roots) == 1 and "SKILL.md" not in files and all("/" in path for path in files):
        (root,) = roots
        files = {path[len(root) + 1 :]: text for path, text in files.items()}
        executables = {path[len(root) + 1 :] for path in executables}
    return files, sorted(executables)


def front_matter(skill_md: str) -> dict[str, object]:
    if not skill_md.startswith("---"):
        raise SkillRefused("SKILL.md must start with its YAML front matter (`---`)")
    end = skill_md.find("\n---", 3)
    if end < 0:
        raise SkillRefused("the YAML front matter of SKILL.md is not closed (`---`)")
    try:
        values = yaml.safe_load(skill_md[3:end]) or {}
    except yaml.YAMLError as error:
        raise SkillRefused(f"the front matter of SKILL.md is not YAML: {error}") from error
    if not isinstance(values, dict):
        raise SkillRefused("the front matter of SKILL.md must be an object")
    return values


def validate(files: dict[str, str], slug: str | None = None) -> FrontMatter:
    """What a skill version must satisfy; returns its name and its description."""
    if not files:
        raise SkillRefused("a skill without any file")
    if len(files) > MAX_FILES:
        raise SkillRefused(f"{len(files)} files: {MAX_FILES} at most")
    for path in files:
        if safe_path(path) != path:
            raise SkillRefused(f"`{path}`: path is not normalised")
    for path, value in files.items():
        if is_inert_binary_path(path):
            try:
                check_inert_binary(path, skill_file_bytes(path, value))
            except ValueError as error:
                raise SkillRefused(str(error)) from error
    if sum(_size(path, value) for path, value in files.items()) > MAX_BYTES:
        raise SkillRefused(f"more than {MAX_BYTES // 1024} KiB")
    if "SKILL.md" not in files:
        raise SkillRefused("SKILL.md is missing at the root")
    values = front_matter(files["SKILL.md"])
    if "allowed-tools" in values or "allowed_tools" in values:
        raise SkillRefused(
            "a skill declares no permission (`allowed-tools`): what an agent may call is decided by "
            "its version and its project"
        )
    name, description = values.get("name"), values.get("description")
    if not isinstance(name, str) or not NAME.match(name):
        raise SkillRefused("`name` is missing, or is not a skill name (lowercase letters, digits, `-`)")
    if slug is not None and name != slug:
        raise SkillRefused(f"SKILL.md is named `{name}`, the skill `{slug}`: the two must match")
    if not isinstance(description, str) or not description.strip():
        raise SkillRefused("`description` is missing: it is what an agent reads to choose the skill")
    return FrontMatter(name=name, description=description.strip())


def digest(files: dict[str, str]) -> str:
    """The digest of a version: the runner recomputes it before laying the files down."""
    return empreinte_de_skill(files)


async def publish_version(  # noqa: PLR0913 - the old `_publier`, moved, plus one switch
    session: AsyncSession,
    principal: Me,
    organisation: Organization,
    files: dict[str, str],
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
    from . import skill_gate, skill_scanner

    try:
        head = validate(files, slug)
    except SkillRefused as refusal:
        raise unprocessable(str(refusal)) from refusal
    # The second scanner (ADR 0095) reads a version a person brings, before any row is touched;
    # what the platform ships is reviewed with its release.
    scanner = None if review_as_platform else skill_scanner.get_scanner()
    scan = await scanner.scan(dict(files), list(executables or [])) if scanner else None
    skill = (
        await session.execute(select(Skill).where(Skill.org_id == organisation.id, Skill.slug == head.name))
    ).scalar_one_or_none()
    assessment = skill_gate.assess(files, executables=executables or [], repo_licences=repo_licences)
    if scan is not None:
        assessment = skill_scanner.with_scan(assessment, scan)
    columns = skill_gate.columns(assessment)
    if review_as_platform:
        if assessment.risk == "high":
            raise unprocessable(
                f"`{head.name}` is assessed high risk: the platform does not approve it on its own word"
            )
        columns = {**columns, "review_status": "approved", "reviewed_by": skill_gate.PLATFORM}
    if skill is None:
        skill = Skill(org_id=organisation.id, slug=head.name, description=head.description, status="active")
        session.add(skill)
        await session.flush()
    skill.description = head.description
    latest = (
        await session.execute(select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill.id))
    ).scalar() or 0
    version = SkillVersion(
        skill_id=skill.id,
        org_id=organisation.id,
        version=latest + 1,
        files=dict(files),
        digest=digest(files),
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


MAX_FICHIERS = MAX_FILES  # lang: fr-ok alias:internal
MAX_OCTETS = MAX_BYTES  # lang: fr-ok alias:internal
NOM = NAME  # lang: fr-ok alias:internal
SkillRefusee = SkillRefused  # lang: fr-ok alias:internal
Entete = FrontMatter  # lang: fr-ok alias:internal
chemin_sur = safe_path  # lang: fr-ok alias:internal
lire_le_zip = read_zip_files  # lang: fr-ok alias:internal
entete = front_matter  # lang: fr-ok alias:internal
valider = validate  # lang: fr-ok alias:internal
empreinte = digest  # lang: fr-ok alias:internal

alias_names(
    __name__,
    {
        "MAX_FICHIERS": "MAX_FILES",  # lang: fr-ok alias:internal
        "MAX_OCTETS": "MAX_BYTES",  # lang: fr-ok alias:internal
        "NOM": "NAME",  # lang: fr-ok alias:internal
        "SkillRefusee": "SkillRefused",  # lang: fr-ok alias:internal
        "Entete": "FrontMatter",  # lang: fr-ok alias:internal
        "chemin_sur": "safe_path",  # lang: fr-ok alias:internal
        "lire_le_zip": "read_zip_files",  # lang: fr-ok alias:internal
        "entete": "front_matter",  # lang: fr-ok alias:internal
        "valider": "validate",  # lang: fr-ok alias:internal
        "empreinte": "digest",  # lang: fr-ok alias:internal
    },
)

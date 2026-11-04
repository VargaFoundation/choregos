# SPDX-License-Identifier: Apache-2.0
"""The agent's skills, placed where its backend reads them (ADR 0033).

The `StageInput` names each skill by its name, its version and its **digest**, set when the
run was prepared. The files come from the internal API; the runner recomputes their digest
before writing anything, and a wrong digest stops the run: a skill modified between
preparation and execution is not the one the agent's version carried.

A backend that does not read skills (`skills_dir = None`) gets them under `.choregos/skills`, with
an index in its prompt. In every case, these files stay out of the diff.
"""

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Any

import yaml
from choregos_contracts import SkillRef, is_inert_binary_path, skill_digest, skill_file_bytes

FALLBACK_DIR = ".choregos/skills"


class InvalidSkill(RuntimeError):  # noqa: N818 - a reasoned refusal, which stops the run
    pass


def _description(skill_md: str) -> str:
    if not skill_md.startswith("---"):
        return ""
    end = skill_md.find("\n---", 3)
    try:
        header = yaml.safe_load(skill_md[3:end]) if end > 0 else {}
    except yaml.YAMLError:
        return ""
    return str((header or {}).get("description") or "").strip() if isinstance(header, dict) else ""


def place_skills(
    delivered: list[dict[str, Any]], expected: list[SkillRef], workspace: Path, skills_dir: str | None
) -> tuple[list[str], str]:
    """Verify and write the skills; return the files written (out of the diff) and the prompt index."""
    by_slug = {str(skill.get("slug")): skill for skill in delivered}
    root = skills_dir or FALLBACK_DIR
    written: list[str] = []
    index: list[str] = []
    for ref in expected:
        skill = by_slug.get(ref.slug)
        if skill is None:
            raise InvalidSkill(f"the skill `{ref.slug}@{ref.version}` was not delivered")
        files: dict[str, str] = dict(skill.get("files") or {})
        try:
            digest = skill_digest(files)
        except ValueError as error:
            raise InvalidSkill(
                f"the skill `{ref.slug}@{ref.version}` carries a corrupt binary: {error}"
            ) from error
        if digest != ref.digest:
            raise InvalidSkill(
                f"the skill `{ref.slug}@{ref.version}` does not have the digest the run expects"
            )
        for path, text in files.items():
            normalised = posixpath.normpath(path)
            if normalised.startswith(("../", "/")) or normalised == "..":
                raise InvalidSkill(f"the skill `{ref.slug}` reaches outside its folder: `{path}`")
            relative = f"{root}/{ref.slug}/{normalised}"
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if is_inert_binary_path(normalised):
                # A font or an image: the bytes, never an exec bit (a plain 0o644 file).
                target.write_bytes(skill_file_bytes(normalised, text))
                target.chmod(0o644)
            else:
                target.write_text(text, encoding="utf-8")
            written.append(relative)
        if skills_dir is None:
            description = _description(files.get("SKILL.md", ""))
            index.append(f"- `{root}/{ref.slug}/SKILL.md`" + (f" — {description}" if description else ""))
    index_text = (
        "## Skills\n\nBefore you act, read the skill that fits the task:\n\n" + "\n".join(index)
        if index
        else ""
    )
    return written, index_text


__all__ = ["FALLBACK_DIR", "InvalidSkill", "place_skills"]

# SPDX-License-Identifier: Apache-2.0
"""Which skill versions an agent may carry (S32-06, ADR 0050).

A high-risk version waits for a second person (`pending_review`). Until then an agent version may
not pin it, and an agent that carries the skill unpinned reads its latest APPROVED version — never
the one waiting.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Skill, SkillVersion

APPROVED = "approved"


async def unusable_skills(session: AsyncSession, org_id: Any, refs: list[Any]) -> list[str]:
    """Why each skill an agent version names cannot be carried; empty when all can.

    `refs` are `AgentSkillRef`s (`slug`, `version` or `None`). A skill absent from the library is
    not judged here: the run says so when it prepares."""
    problemes: list[str] = []
    for ref in refs:
        lignes = (
            await session.execute(
                select(SkillVersion.version, SkillVersion.review_status)
                .join(Skill, Skill.id == SkillVersion.skill_id)
                .where(Skill.org_id == org_id, Skill.slug == ref.slug)
            )
        ).tuples()
        etats = dict(lignes.all())
        if not etats:
            continue
        if ref.version is not None:
            etat = etats.get(ref.version)
            if etat is not None and etat != APPROVED:
                problemes.append(
                    f"skill `{ref.slug}` v{ref.version} is high risk and waits for a second reviewer"
                )
        elif all(etat != APPROVED for etat in etats.values()):
            problemes.append(
                f"skill `{ref.slug}` has no approved version yet: it waits for a second reviewer"
            )
    return problemes


__all__ = ["APPROVED", "unusable_skills"]

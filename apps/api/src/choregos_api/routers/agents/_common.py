# SPDX-License-Identifier: Apache-2.0
"""Helpers shared by several route groups of the agents router."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import (
    Organization,
)
from ...deps import Me
from ...errors import forbidden, unprocessable
from ...schemas import (
    AgentSpec,
)
from ...services.agent_governance import (
    is_admin,
)
from ...services.skill_review import unusable_skills


async def _skills_usable(session: AsyncSession, organisation: Organization, spec: AgentSpec) -> None:
    """A version may not carry a skill version that waits for its second reviewer (S32-06)."""
    problemes = await unusable_skills(session, organisation.id, list(spec.skills))
    if problemes:
        raise unprocessable("; ".join(problemes))


async def _model_allowed(session: AsyncSession, organisation: Organization, spec: AgentSpec) -> None:
    """A version may not name a model the organisation's catalogue does not allow (ADR 0060)."""
    from ...services.model_catalogue import load_policy, refusal_for_spec

    refusal = refusal_for_spec(await load_policy(session, organisation.id), spec)
    if refusal is not None:
        raise unprocessable(refusal, [{"loc": ["spec", "model"], "msg": refusal}])


def _admin(principal: Me, org: str) -> None:
    if not is_admin(principal, org):
        raise forbidden("this needs the org_admin role")

# SPDX-License-Identifier: Apache-2.0
"""Knowledge needs bound to the organisation's bases, by reference (S66-12, ADR 0159).

A manifest's `knowledge` item is a seed (copied at install, `offered_knowledge`) or a binding need
(`bind: {description, required}`). A binding need is bound to existing bases of the organisation: one
`assistant_knowledge_bindings` row per base, nothing copied, nothing of the base changed.

Four rules, each decided here and nowhere else:

- **Who binds.** Adding a base needs that the caller may read it now (`knowledge.access.decide`, ADR 0081)
  and that it is `active`; a base they may not read and one that does not exist get the same 422. Keeping
  or removing a base needs no right on it. (`assistant:manage` itself is checked by
  `assistants.bind_knowledge`.)
- **Who reads.** At each turn, a bound base is served only when the conversation's person may read it at
  that moment (`visible_to` with the base's grants, the person rebuilt from the database as ADR 0097 does for
  a run's initiator); otherwise the turn drops it (`served_bases`).
- **Readiness.** A required binding need with no enabled binding to an active base is `knowledge_unbound`.
- **Upgrades** (S63-22): a need the manifest dropped has its bindings disabled and kept; one it adds is
  unbound; a `required` flag that moved is reported (`upgrade`).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import (
    AgentSessionRecord,
    AssistantInstallation,
    AssistantKnowledgeBinding,
    KnowledgeBase,
    KnowledgeGrant,
    Organization,
    User,
)
from ..errors import not_found, unprocessable
from ..knowledge.access import KnowledgeAction, decide, visible_to
from ..knowledge.on_behalf import principal_in_org
from ..rbac import Principal
from ..schemas.assistants import AssistantKnowledgeBinding as BindingDto
from ..schemas.assistants import AssistantReadinessItem

#: The serving modes a conversation reads (`conversation_knowledge`).
SERVED_MODES = ("index", "files")


def binding_needs(document: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The manifest's binding needs by name: `{name: bind}`; seeds are left out."""
    found: dict[str, dict[str, Any]] = {}
    for item in (document or {}).get("knowledge") or []:
        if isinstance(item, dict) and isinstance(item.get("bind"), dict) and item.get("name"):
            found[str(item["name"])] = item["bind"]
    return found


def _required(bind: dict[str, Any]) -> bool:
    return bool(bind.get("required", True))


async def _rows(
    session: AsyncSession, row: AssistantInstallation
) -> list[tuple[AssistantKnowledgeBinding, KnowledgeBase]]:
    found = await session.execute(
        select(AssistantKnowledgeBinding, KnowledgeBase)
        .join(KnowledgeBase, KnowledgeBase.id == AssistantKnowledgeBinding.knowledge_base_id)
        .where(AssistantKnowledgeBinding.installation_id == row.id)
        .order_by(AssistantKnowledgeBinding.name, KnowledgeBase.slug)
    )
    return [(b, k) for b, k in found.all()]


async def bindings_of(session: AsyncSession, row: AssistantInstallation) -> list[BindingDto]:
    """What the installation's managers see: every bound base's slug (they must be able to remove one)."""
    grouped: dict[str, list[tuple[AssistantKnowledgeBinding, KnowledgeBase]]] = defaultdict(list)
    for binding, base in await _rows(session, row):
        grouped[binding.name].append((binding, base))
    return [
        BindingDto(name=name, bases=[b.slug for _, b in pairs], enabled=any(x.enabled for x, _ in pairs))
        for name, pairs in grouped.items()
    ]


async def readiness_items(
    session: AsyncSession, row: AssistantInstallation, document: dict[str, Any]
) -> list[AssistantReadinessItem]:
    """A required binding need with no enabled binding to an active base."""
    bound = {
        binding.name
        for binding, base in await _rows(session, row)
        if binding.enabled and base.status == "active"
    }
    return [
        AssistantReadinessItem(
            kind="knowledge_unbound",
            need=name,
            message=f"the required knowledge `{name}` is not bound to a knowledge base of the organisation",
            fix_by="owners",
            groups=list(row.owner_groups),
        )
        for name, bind in binding_needs(document).items()
        if _required(bind) and name not in bound
    ]


async def _grants(session: AsyncSession, base_ids: list[str]) -> dict[str, list[KnowledgeGrant]]:
    found: dict[str, list[KnowledgeGrant]] = defaultdict(list)
    if base_ids:
        rows = await session.execute(
            select(KnowledgeGrant).where(KnowledgeGrant.knowledge_base_id.in_(base_ids))
        )
        for grant in rows.scalars():
            found[grant.knowledge_base_id].append(grant)
    return found


async def bind(
    session: AsyncSession,
    org: str,
    row: AssistantInstallation,
    document: dict[str, Any],
    name: str,
    slugs: list[str],
    principal: Principal,
) -> bool:
    """Binds the need `name` to exactly the bases `slugs` (the whole list). Returns whether anything changed.
    Every base added must be active and readable by `principal`; on any refusal nothing changes."""
    if name not in binding_needs(document):
        raise not_found("Knowledge need", name)
    wanted = list(dict.fromkeys(slugs))
    current = [(b, k) for b, k in await _rows(session, row) if b.name == name]
    held = {base.slug for _, base in current}
    added = [slug for slug in wanted if slug not in held]
    bases: dict[str, KnowledgeBase] = {}
    if added:
        found = await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.org_id == row.org_id, KnowledgeBase.slug.in_(added))
        )
        bases = {b.slug: b for b in found.scalars()}
    grants = await _grants(session, [b.id for b in bases.values()])
    problems: list[dict[str, Any]] = []
    for slug in added:
        base = bases.get(slug)
        # A base the caller may not read and one that does not exist: one answer (ADR 0081, ADR 0159 point 3).
        if (
            base is None
            or not decide(principal, org, base, KnowledgeAction.READ, grants.get(base.id, [])).allowed
        ):
            problems.append(
                {"loc": ["bases", slug], "msg": f"`{slug}` is not a knowledge base of {org} you may read"}
            )
        elif base.status != "active" or base.serving_mode not in SERVED_MODES:
            problems.append(
                {"loc": ["bases", slug], "msg": f"`{slug}` is {base.status}: only an active base is bound"}
            )
    if problems:
        raise unprocessable(f"the knowledge `{name}` cannot be bound to these bases", problems)
    changed = False
    for binding, base in current:
        if base.slug not in wanted:
            await session.delete(binding)
            changed = True
        elif not binding.enabled:  # a need that came back after an upgrade dropped it: bound again
            binding.enabled = True
            changed = True
    for slug in added:
        session.add(
            AssistantKnowledgeBinding(
                org_id=row.org_id,
                installation_id=row.id,
                name=name,
                knowledge_base_id=bases[slug].id,
                bound_by=principal.email,
            )
        )
        changed = True
    await session.flush()
    if changed:
        await record(
            session,
            principal,
            "assistant.knowledge.bind",
            org_id=row.org_id,
            target_type="assistant",
            target_id=row.slug,
            need=name,
            added=added,
            removed=sorted(held - set(wanted)),
        )
    return changed


async def upgrade(
    session: AsyncSession,
    row: AssistantInstallation,
    document: dict[str, Any],
    previous: dict[str, Any] | None,
) -> list[tuple[str, str, str]]:
    """Brings the bindings in line with the manifest the plugin now ships: `(need, kind, message)` per change.
    A need dropped (or turned into a seed) has its bindings disabled and kept; one added is unbound; a
    `required` flag that moved is reported."""
    now, before = binding_needs(document), binding_needs(previous)
    changes: list[tuple[str, str, str]] = []
    rows = await _rows(session, row)
    dropped: set[str] = set()
    for binding, _ in rows:
        if binding.name not in now and binding.enabled:
            binding.enabled = False
            dropped.add(binding.name)
    changes.extend(
        (
            name,
            "knowledge_removed",
            f"the knowledge `{name}` is no longer a need: its bases are no longer read",
        )
        for name in sorted(dropped)
    )
    bound = {b.name for b, _ in rows}
    for name, bind_decl in now.items():
        if name not in before:
            if name not in bound:
                kind = "required" if _required(bind_decl) else "optional"
                changes.append(
                    (name, "knowledge_added", f"the knowledge `{name}` is a new {kind} need, not bound yet")
                )
        elif _required(before[name]) != _required(bind_decl):
            state = "required" if _required(bind_decl) else "optional"
            changes.append((name, "knowledge_required_changed", f"the knowledge `{name}` is now {state}"))
    await session.flush()
    return changes


async def _person(session: AsyncSession, record_: AgentSessionRecord) -> tuple[Principal | None, str]:
    """The conversation's person in its organisation, rebuilt from the database (ADR 0097), and its slug."""
    organisation = await session.get(Organization, record_.org_id)
    slug = organisation.slug if organisation is not None else ""
    email = (
        (await session.execute(select(User.email).where(User.id == record_.user_id))).scalar_one_or_none()
        if record_.user_id
        else None
    )
    if email is None:
        return None, slug
    return await principal_in_org(session, record_.org_id, email), slug


async def served_bases(session: AsyncSession, record_: AgentSessionRecord) -> list[KnowledgeBase]:
    """The bound bases this turn reads: enabled, active, and readable by the conversation's person now.
    A base the person may not read is dropped from the turn, whatever the installation says."""
    if record_.kind != "conversation" or not record_.installation_id:
        return []
    found = await session.execute(
        select(KnowledgeBase)
        .join(AssistantKnowledgeBinding, AssistantKnowledgeBinding.knowledge_base_id == KnowledgeBase.id)
        .where(
            AssistantKnowledgeBinding.installation_id == record_.installation_id,
            AssistantKnowledgeBinding.enabled.is_(True),
            KnowledgeBase.org_id == record_.org_id,
            KnowledgeBase.status == "active",
            KnowledgeBase.serving_mode.in_(SERVED_MODES),
        )
        .order_by(KnowledgeBase.slug)
    )
    bases = list({b.id: b for b in found.scalars()}.values())
    if not bases:
        return []
    person, org = await _person(session, record_)
    if person is None:
        return []
    grants = await _grants(session, [b.id for b in bases])
    return [b for b in bases if visible_to(person, org, b, grants.get(b.id, []))]

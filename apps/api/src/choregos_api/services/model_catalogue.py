# SPDX-License-Identifier: Apache-2.0
"""The organisation's model catalogue (ADR 0060): load it as a policy, sync it from the gateway,
and say what a switch to `curated` would refuse."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from choregos_core import CatalogueEntry, GatewayModel, ModelPolicy, Spend, utcnow
from choregos_core.models import PLATFORM_PROFILES, TIERS, USAGES
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, AgentVersion, ModelCatalogueRow, Organization, Project
from ..schemas.agents import AgentSpec

MODES = ("open", "curated")


def org_settings(organisation: Organization) -> dict[str, Any]:
    models = (organisation.settings or {}).get("models") or {}
    return {"mode": models.get("mode", "open"), "tiers": dict(models.get("tiers") or {})}


def save_settings(organisation: Organization, *, mode: str, tiers: dict[str, str | None]) -> None:
    """Reassign the whole JSON: an in-place edit of a JSON column is not seen as a change."""
    organisation.settings = {
        **(organisation.settings or {}),
        "models": {"mode": mode, "tiers": {t: tiers.get(t) or None for t in TIERS}},
    }


async def load_policy(session: AsyncSession, org_id: str) -> ModelPolicy:
    organisation = await session.get(Organization, org_id)
    if organisation is None:
        return ModelPolicy()
    settings = org_settings(organisation)
    rows = (
        (await session.execute(select(ModelCatalogueRow).where(ModelCatalogueRow.org_id == org_id)))
        .scalars()
        .all()
    )
    return ModelPolicy(
        mode=settings["mode"],
        entries={
            row.model_name: CatalogueEntry(
                enabled=row.enabled,
                allowed_roles=tuple(row.allowed_roles or ()),
                usages=tuple(row.usages or ()),
                disabled_by=(row.updated_by or "") if not row.enabled else "",
            )
            for row in rows
        },
        tiers=settings["tiers"],
    )


def facts_of(model: GatewayModel) -> dict[str, Any]:
    return {
        "provider": model.provider,
        "upstream_model": model.litellm_model,
        "input_cost_per_1k": model.input_cost_per_1k,
        "output_cost_per_1k": model.output_cost_per_1k,
        "max_input_tokens": model.max_input_tokens,
        "supports_tool_calling": model.supports_tool_calling,
        "supports_vision": model.supports_vision,
    }


async def sync(
    session: AsyncSession, organisation: Organization, models: Iterable[GatewayModel], who: str
) -> dict[str, int]:
    """Upsert the gateway's models by name. A new model arrives enabled in `open` mode and disabled
    in `curated` mode; what the admin decided on an existing row is kept; a row the gateway no
    longer lists gets `missing_since` and is never deleted."""
    mode = org_settings(organisation)["mode"]
    now = utcnow()
    existing = {
        row.model_name: row
        for row in (
            await session.execute(
                select(ModelCatalogueRow).where(ModelCatalogueRow.org_id == organisation.id)
            )
        ).scalars()
    }
    seen: set[str] = set()
    added = 0
    for model in models:
        seen.add(model.model_name)
        row = existing.get(model.model_name)
        if row is None:
            session.add(
                ModelCatalogueRow(
                    org_id=organisation.id,
                    model_name=model.model_name,
                    display_name=model.model_name,
                    source="gateway",
                    enabled=mode == "open",
                    allowed_roles=[],
                    usages=["agent"],
                    gateway_facts=facts_of(model),
                    last_seen_at=now,
                    notes="",
                    updated_by=who,
                )
            )
            added += 1
        else:
            row.gateway_facts = facts_of(model)
            row.last_seen_at = now
            row.missing_since = None
            if row.source == "manual":  # the gateway lists it now: it is the gateway's
                row.source = "gateway"
    missing = 0
    for name, row in existing.items():
        if name not in seen and row.source == "gateway" and row.missing_since is None:
            row.missing_since = now
            missing += 1
    await session.flush()
    return {"added": added, "missing": missing, "seen": len(seen)}


def models_named_by(spec: AgentSpec, policy: ModelPolicy) -> list[str]:
    """The model names a spec's `model` field resolves to for this organisation."""
    requested = spec.model
    if not requested:
        return []
    if requested == "profile:by_size":
        tiers = ["standard", "strong"]
    elif requested.startswith("profile:"):
        tiers = [requested.removeprefix("profile:")]
    else:
        return [requested]
    names = []
    for tier in tiers:
        named = policy.tier_model(tier)
        if named is None and tier in PLATFORM_PROFILES:
            named = PLATFORM_PROFILES[tier].litellm_model
        if named:
            names.append(named)
    return names


def refusal_for_spec(policy: ModelPolicy, spec: AgentSpec) -> str | None:
    """Why an agent version may not name its model, or `None`. A project may remap a profile
    (checked where the project's profiles are written)."""
    for name in models_named_by(spec, policy):
        refusal = policy.check(name, None, usage="agent")
        if refusal is not None:
            return refusal
    return None


async def preview_curation(session: AsyncSession, organisation: Organization) -> list[dict[str, str]]:
    """What switching to `curated` would refuse today: agent versions and project profiles that name
    a model with no enabled entry."""
    entries = (
        (await session.execute(select(ModelCatalogueRow).where(ModelCatalogueRow.org_id == organisation.id)))
        .scalars()
        .all()
    )
    policy = ModelPolicy(
        mode="curated",
        entries={
            r.model_name: CatalogueEntry(
                enabled=r.enabled,
                allowed_roles=tuple(r.allowed_roles or ()),
                usages=tuple(r.usages or ()),
                disabled_by=r.updated_by or "",
            )
            for r in entries
        },
        tiers=org_settings(organisation)["tiers"],
    )
    refused: list[dict[str, str]] = []
    latest = (
        await session.execute(
            select(Agent.slug, AgentVersion.version, AgentVersion.spec)
            .join(AgentVersion, AgentVersion.agent_id == Agent.id)
            .where(Agent.org_id == organisation.id, Agent.status == "active")
            .order_by(Agent.slug, AgentVersion.version.desc())
        )
    ).all()
    done: set[str] = set()
    for slug, _version, raw in latest:
        if slug in done:
            continue
        done.add(slug)
        spec = AgentSpec.model_validate(raw)
        for name in models_named_by(spec, policy):
            if policy.check(name, None, usage="agent") is not None:
                refused.append({"kind": "agent", "ref": slug, "model": name})
    projects = (await session.execute(select(Project).where(Project.org_id == organisation.id))).scalars()
    for project in projects:
        profiles = ((project.config or {}).get("models") or {}).get("profiles") or {}
        for tier, profile in sorted(profiles.items()):
            profile_model = (profile or {}).get("litellm_model") if isinstance(profile, dict) else None
            if profile_model and policy.check(profile_model, None, usage="agent") is not None:
                refused.append(
                    {"kind": "project_profile", "ref": f"{project.slug}/{tier}", "model": profile_model}
                )
    return refused


async def priced_spend(
    session: AsyncSession, org_id: str, models: Iterable[str], spend: Spend, *, measured: bool
) -> tuple[Spend, dict[str, Any]]:
    """The spend with the organisation's price override applied, and how it was priced.

    An override replaces the gateway's figure (tokens x the organisation's price per 1k) when the
    gateway reported tokens; the gateway's own figure is returned in `gateway_cost_usd`. A gateway
    that cannot read a spend back (`measured` false) reports no tokens: the line is `unmeasured` and
    the override changes nothing.
    """
    info: dict[str, Any] = {"priced_by": "gateway" if measured else "none"}
    if not measured:
        info["unmeasured"] = True
        return spend, info
    names = [n for n in models if n]
    rows = (
        (
            await session.execute(
                select(ModelCatalogueRow).where(
                    ModelCatalogueRow.org_id == org_id, ModelCatalogueRow.model_name.in_(names)
                )
            )
        )
        .scalars()
        .all()
    )
    row = next(
        (
            r
            for n in names
            for r in rows
            if r.model_name == n
            and r.input_per_1k_override is not None
            and r.output_per_1k_override is not None
        ),
        None,
    )
    if row is None or not (spend.tokens_in or spend.tokens_out):
        return spend, info
    price_in = row.input_per_1k_override or 0.0
    price_out = row.output_per_1k_override or 0.0
    cost = round(spend.tokens_in / 1000 * price_in + spend.tokens_out / 1000 * price_out, 6)
    info.update({"priced_by": "override", "gateway_cost_usd": spend.cost_usd})
    return spend.model_copy(update={"cost_usd": cost}), info


def usable_names(policy: ModelPolicy, names: Iterable[str], role: str | None, usage: str) -> list[str]:
    return [n for n in names if policy.check(n, None, role=role, usage=usage) is None]


__all__ = ["MODES", "TIERS", "USAGES"]


@dataclass(frozen=True)
class Price:
    """What a model costs per 1 000 tokens, and who says so: the organisation's override, the
    gateway's facts, or nobody (`none`: the spend cannot be told)."""

    input_per_1k: float | None
    output_per_1k: float | None
    priced_by: Literal["gateway", "override", "none"]


def price_from(row: ModelCatalogueRow | None, facts: dict[str, Any] | None) -> Price:
    """The one rule of price resolution (ADR 0060): an override (both prices) wins, then the gateway's
    facts (the catalogue row's, else the gateway's live ones), else none."""
    if row is not None and row.input_per_1k_override is not None and row.output_per_1k_override is not None:
        return Price(row.input_per_1k_override, row.output_per_1k_override, "override")
    if facts is None and row is not None:
        facts = row.gateway_facts or None
    if facts and (facts.get("input_cost_per_1k") is not None or facts.get("output_cost_per_1k") is not None):
        return Price(facts.get("input_cost_per_1k"), facts.get("output_cost_per_1k"), "gateway")
    return Price(None, None, "none")


async def price_of(session: AsyncSession, org_id: str, model: str) -> Price:
    """The price of `model` for an organisation: its catalogue row, else (open mode) the gateway's."""
    row = (
        await session.execute(
            select(ModelCatalogueRow).where(
                ModelCatalogueRow.org_id == org_id, ModelCatalogueRow.model_name == model
            )
        )
    ).scalar_one_or_none()
    price = price_from(row, None)
    if price.priced_by != "none":
        return price
    organisation = await session.get(Organization, org_id)
    if organisation is None or org_settings(organisation)["mode"] != "open":
        return price
    try:
        from .gateway import org_gateway

        gateway = await org_gateway(session, org_id)
        for listed in await gateway.list_models():
            if listed.model_name == model:
                return price_from(
                    row,
                    {
                        "input_cost_per_1k": listed.input_cost_per_1k,
                        "output_cost_per_1k": listed.output_cost_per_1k,
                    },
                )
    except Exception:  # an unreachable gateway leaves the model unpriced, it does not stop an estimate
        return price
    return price

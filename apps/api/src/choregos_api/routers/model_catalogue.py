# SPDX-License-Identifier: Apache-2.0
"""The organisation's model catalogue (S39-07, ADR 0060).

What the gateway routes is the gateway's; which of it the organisation lets its agents use, for
which stage roles and usages, at which price, and which model backs each tier, is decided here —
whatever the gateway is. Reads are for every member; changes need `model:catalogue:manage`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from choregos_core import utcnow
from choregos_core.models import PLATFORM_PROFILES, TIERS
from fastapi import APIRouter, Path, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import ModelCatalogueRow, Organization
from ..deps import Db, Me
from ..errors import conflict, forbidden, not_found, unprocessable
from ..rbac import Permission
from ..schemas.definitions import (
    ModelCatalogue,
    ModelCatalogueEntry,
    ModelCatalogueEntryCreate,
    ModelCatalogueGateway,
    ModelCataloguePatch,
    ModelCatalogueSettings,
    ModelCuratePreview,
    ModelCurationRefusal,
    ModelGatewayFacts,
    ModelSyncResult,
    ModelTiers,
    PriceOverride,
    UsableModel,
)
from ..services import model_catalogue as service
from ..services.gateway import org_gateway, org_gateway_and_type

router = APIRouter(tags=["models"])

EntryId = Annotated[str, Path(min_length=1, max_length=64)]


async def _organisation(session: AsyncSession, org: str) -> Organization:
    found = (await session.execute(select(Organization).where(Organization.slug == org))).scalar_one_or_none()
    if found is None:
        raise not_found("Organisation", org)
    return found


def _read(principal: Me, org: str) -> None:
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"reading the model catalogue of {org} requires being a member of it")


def _manage(principal: Me, org: str) -> None:
    if not principal.can(Permission.MODEL_CATALOGUE_MANAGE, org):
        raise forbidden(f"the model catalogue of {org} is decided by its administrator")


def _dto(row: ModelCatalogueRow) -> ModelCatalogueEntry:
    facts = row.gateway_facts or {}
    override = (
        PriceOverride(input_per_1k=row.input_per_1k_override, output_per_1k=row.output_per_1k_override)
        if row.input_per_1k_override is not None and row.output_per_1k_override is not None
        else None
    )
    return ModelCatalogueEntry(
        id=row.id,
        model_name=row.model_name,
        display_name=row.display_name or row.model_name,
        source=row.source,
        enabled=row.enabled,
        allowed_roles=list(row.allowed_roles or []),
        usages=list(row.usages or []),
        price_override=override,
        max_usd_per_minute=row.max_usd_per_minute,
        gateway=ModelGatewayFacts.model_validate(facts) if facts else None,
        last_seen_at=row.last_seen_at,
        missing_since=row.missing_since,
        notes=row.notes or "",
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


async def _rows(session: AsyncSession, organisation: Organization) -> list[ModelCatalogueRow]:
    return list(
        (
            await session.execute(
                select(ModelCatalogueRow)
                .where(ModelCatalogueRow.org_id == organisation.id)
                .order_by(ModelCatalogueRow.model_name)
            )
        ).scalars()
    )


async def _row(session: AsyncSession, organisation: Organization, entry_id: str) -> ModelCatalogueRow:
    row = await session.get(ModelCatalogueRow, entry_id)
    if row is None or row.org_id != organisation.id:
        raise not_found("Model catalogue entry", entry_id)
    return row


async def _gateway_info(session: AsyncSession, organisation: Organization) -> ModelCatalogueGateway:
    kind, gateway = await org_gateway_and_type(session, organisation.id)
    return ModelCatalogueGateway(type=kind, capabilities=sorted(gateway.capabilities))


@router.get("/orgs/{org}/models", response_model=ModelCatalogue, operation_id="getModelCatalogue")
async def get_catalogue(org: str, session: Db, principal: Me) -> ModelCatalogue:
    _read(principal, org)
    organisation = await _organisation(session, org)
    settings = service.org_settings(organisation)
    return ModelCatalogue(
        mode=settings["mode"],
        gateway=await _gateway_info(session, organisation),
        tiers=ModelTiers.model_validate({t: settings["tiers"].get(t) for t in TIERS}),
        entries=[_dto(r) for r in await _rows(session, organisation)],
    )


@router.put(
    "/orgs/{org}/models/settings",
    response_model=ModelCatalogueSettings,
    operation_id="putModelCatalogueSettings",
)
async def put_settings(
    org: str, body: ModelCatalogueSettings, session: Db, principal: Me
) -> ModelCatalogueSettings:
    _manage(principal, org)
    organisation = await _organisation(session, org)
    before = service.org_settings(organisation)
    tiers = body.tiers.model_dump()
    rows = {r.model_name: r for r in await _rows(session, organisation)}
    for tier, name in tiers.items():
        if not name:
            continue
        row = rows.get(name)
        if body.mode == "curated" and (row is None or not row.enabled):
            raise unprocessable(
                f"the tier `{tier}` names `{name}`, which is not an enabled model of the catalogue",
                [{"loc": ["tiers", tier], "msg": "model not enabled"}],
            )
    service.save_settings(organisation, mode=body.mode, tiers=tiers)
    await record(
        session,
        principal,
        "models.catalogue.settings",
        org_id=organisation.id,
        target_type="organisation",
        target_id=organisation.id,
        mode_before=before["mode"],
        mode=body.mode,
        tiers=tiers,
    )
    return ModelCatalogueSettings(mode=body.mode, tiers=body.tiers)


@router.post("/orgs/{org}/models/sync", response_model=ModelSyncResult, operation_id="syncModelCatalogue")
async def sync_catalogue(org: str, session: Db, principal: Me) -> ModelSyncResult:
    _manage(principal, org)
    organisation = await _organisation(session, org)
    gateway = await org_gateway(session, organisation.id)
    if "list_models" not in gateway.capabilities:
        raise conflict("this gateway cannot list its models: add them by hand")
    try:
        models = await gateway.list_models()
    except Exception as error:  # the gateway is another service: say so, do not fail with a 500
        from ..errors import upstream

        raise upstream("gateway", f"the gateway did not list its models: {str(error)[:300]}") from error
    result = await service.sync(session, organisation, models, principal.email)
    await record(
        session, principal, "models.catalogue.sync", org_id=organisation.id, target_type="organisation",
        target_id=organisation.id, **result,
    )  # fmt: skip
    return ModelSyncResult(**result)


@router.post(
    "/orgs/{org}/models",
    response_model=ModelCatalogueEntry,
    status_code=status.HTTP_201_CREATED,
    operation_id="addModelCatalogueEntry",
)
async def add_entry(
    org: str, body: ModelCatalogueEntryCreate, session: Db, principal: Me
) -> ModelCatalogueEntry:
    """A model the gateway routes but does not list (a wildcard route, a private deployment)."""
    _manage(principal, org)
    organisation = await _organisation(session, org)
    if any(r.model_name == body.model_name for r in await _rows(session, organisation)):
        raise conflict(f"`{body.model_name}` is already in the catalogue")
    row = ModelCatalogueRow(
        org_id=organisation.id,
        model_name=body.model_name,
        display_name=body.display_name or body.model_name,
        source="manual",
        enabled=body.enabled,
        allowed_roles=list(body.allowed_roles),
        usages=list(body.usages),
        input_per_1k_override=body.price_override.input_per_1k if body.price_override else None,
        output_per_1k_override=body.price_override.output_per_1k if body.price_override else None,
        max_usd_per_minute=body.max_usd_per_minute,
        gateway_facts={},
        notes=body.notes,
        updated_by=principal.email,
    )
    session.add(row)
    await session.flush()
    await record(
        session, principal, "models.catalogue.add", org_id=organisation.id, target_type="model",
        target_id=row.id, model=row.model_name,
    )  # fmt: skip
    return _dto(row)


@router.patch(
    "/orgs/{org}/models/{entry_id}",
    response_model=ModelCatalogueEntry,
    operation_id="updateModelCatalogueEntry",
)
async def update_entry(
    org: str, entry_id: EntryId, body: ModelCataloguePatch, session: Db, principal: Me
) -> ModelCatalogueEntry:
    _manage(principal, org)
    organisation = await _organisation(session, org)
    row = await _row(session, organisation, entry_id)
    changes = body.model_dump(
        exclude_unset=True, exclude={"clear_price_override", "clear_max_usd_per_minute"}
    )
    if body.display_name is not None:
        row.display_name = body.display_name
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.allowed_roles is not None:
        row.allowed_roles = list(body.allowed_roles)
    if body.usages is not None:
        row.usages = list(body.usages)
    if body.notes is not None:
        row.notes = body.notes
    if body.clear_price_override:
        row.input_per_1k_override = row.output_per_1k_override = None
    elif body.price_override is not None:
        row.input_per_1k_override = body.price_override.input_per_1k
        row.output_per_1k_override = body.price_override.output_per_1k
    if body.clear_max_usd_per_minute:
        row.max_usd_per_minute = None
    elif body.max_usd_per_minute is not None:
        row.max_usd_per_minute = body.max_usd_per_minute
    row.updated_by = principal.email
    row.updated_at = utcnow()
    await session.flush()
    await record(
        session, principal, "models.catalogue.update", org_id=organisation.id, target_type="model",
        target_id=row.id, model=row.model_name, changes=sorted(changes),
    )  # fmt: skip
    return _dto(row)


@router.delete(
    "/orgs/{org}/models/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteModelCatalogueEntry",
)
async def delete_entry(org: str, entry_id: EntryId, session: Db, principal: Me) -> Response:
    _manage(principal, org)
    organisation = await _organisation(session, org)
    row = await _row(session, organisation, entry_id)
    if row.source != "manual":
        raise conflict("an entry the gateway lists cannot be deleted: disable it instead")
    await session.delete(row)
    await record(
        session, principal, "models.catalogue.delete", org_id=organisation.id, target_type="model",
        target_id=entry_id, model=row.model_name,
    )  # fmt: skip
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/orgs/{org}/models/curate-preview",
    response_model=ModelCuratePreview,
    operation_id="previewModelCuration",
)
async def curate_preview(org: str, session: Db, principal: Me) -> ModelCuratePreview:
    """What switching to `curated` would refuse today, before anybody does it."""
    _manage(principal, org)
    organisation = await _organisation(session, org)
    refused = await service.preview_curation(session, organisation)
    return ModelCuratePreview(refused=[ModelCurationRefusal.model_validate(r) for r in refused])


@router.get("/orgs/{org}/models/usable", response_model=list[UsableModel], operation_id="listUsableModels")
async def usable_models(
    org: str,
    session: Db,
    principal: Me,
    role: Annotated[str | None, Query()] = None,
    usage: Annotated[str, Query(pattern="^(agent|embed|distill|eval)$")] = "agent",
) -> list[UsableModel]:
    """The models an agent may be given: what the catalogue enables for this role and usage, plus,
    in `open` mode, what the gateway lists that the catalogue does not mention."""
    _read(principal, org)
    organisation = await _organisation(session, org)
    settings = service.org_settings(organisation)
    policy = await service.load_policy(session, organisation.id)
    rows = {r.model_name: r for r in await _rows(session, organisation)}
    live: dict[str, ModelGatewayFacts] = {}
    if settings["mode"] == "open":
        try:
            gateway = await org_gateway(session, organisation.id)
            for m in await gateway.list_models():
                live[m.model_name] = ModelGatewayFacts(
                    provider=m.provider,
                    upstream_model=m.litellm_model,
                    input_cost_per_1k=m.input_cost_per_1k,
                    output_cost_per_1k=m.output_cost_per_1k,
                    max_input_tokens=m.max_input_tokens,
                    supports_tool_calling=m.supports_tool_calling,
                    supports_vision=m.supports_vision,
                )
        except Exception:  # the catalogue alone still answers
            live = {}
    tiers = settings["tiers"]
    out: list[UsableModel] = []
    for name in sorted(set(rows) | set(live)):
        if policy.check(name, None, role=role, usage=usage) is not None:
            continue
        row = rows.get(name)
        facts = (
            ModelGatewayFacts.model_validate(row.gateway_facts)
            if row and row.gateway_facts
            else live.get(name)
        )
        entry = _dto(row) if row else None
        price_in: float | None
        price_out: float | None
        by: Literal["gateway", "override", "none"]
        if entry is not None and entry.price_override is not None:
            price_in, price_out, by = (
                entry.price_override.input_per_1k,
                entry.price_override.output_per_1k,
                "override",
            )
        elif facts is not None and (
            facts.input_cost_per_1k is not None or facts.output_cost_per_1k is not None
        ):
            price_in, price_out, by = facts.input_cost_per_1k, facts.output_cost_per_1k, "gateway"
        else:
            price_in, price_out, by = None, None, "none"
        out.append(
            UsableModel(
                model_name=name,
                display_name=(entry.display_name if entry else name),
                provider=facts.provider if facts else "",
                input_cost_per_1k=price_in,
                output_cost_per_1k=price_out,
                priced_by=by,
                tiers=[
                    t
                    for t in TIERS
                    if (
                        tiers.get(t)
                        or (PLATFORM_PROFILES[t].litellm_model if t in PLATFORM_PROFILES else None)
                    )
                    == name
                ],
            )
        )
    return out

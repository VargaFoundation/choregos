# SPDX-License-Identifier: Apache-2.0
"""Modèles : catalogue plateforme, profils projet, matrice backend × modèle."""

from __future__ import annotations

from choregos_contracts import ProjectConfig
from choregos_core import ModelResolver
from choregos_core.models import PLATFORM_PROFILES
from fastapi import APIRouter
from sqlalchemy import select

from ..audit import record
from ..db.models import ModelProfileRow, Project
from ..deps import Db, ProjectCtx
from ..errors import unprocessable
from ..rbac import Permission
from ..schemas import (
    GatewayModelDto,
    ModelMatrix,
    ModelMatrixEntry,
    ProjectModelProfile,
    ProjectModels,
)

router = APIRouter(tags=["models"])


async def _gateway_models() -> list[GatewayModelDto]:
    from choregos_adapters import build
    from choregos_adapters.registry import default_gateway_type

    # The deployment's gateway (`CHOREGOS_GATEWAY_TYPE`), not LiteLLM whatever was configured.
    gateway = build("gateway", default_gateway_type(), {})
    models = await gateway.list_models()
    return [GatewayModelDto.model_validate(m.model_dump()) for m in models]


@router.get("/platform/models", response_model=list[GatewayModelDto], operation_id="listPlatformModels")
async def platform_models() -> list[GatewayModelDto]:
    return await _gateway_models()


@router.get("/projects/{id}/models", response_model=ProjectModels, operation_id="getProjectModels")
async def get_models(ctx: ProjectCtx) -> ProjectModels:
    config = ProjectConfig.model_validate(ctx.project.config)
    return ProjectModels(
        profiles={
            name: ProjectModelProfile(
                litellm_model=profile.litellm_model,
                params=profile.params,
                max_turns_factor=profile.max_turns_factor,
            )
            for name, profile in config.models.profiles.items()
        },
        allow_unvalidated=config.models.allow_unvalidated,
        inherited={name: profile.litellm_model for name, profile in PLATFORM_PROFILES.items()},
    )


@router.put("/projects/{id}/models", response_model=ProjectModels, operation_id="putProjectModels")
async def put_models(ctx: ProjectCtx, body: ProjectModels, session: Db) -> ProjectModels:
    """Refuse une combinaison backend × modèle non validée, sauf `allow_unvalidated`."""
    ctx.require(Permission.MODELS_WRITE)
    # The same row lock as `PATCH /projects/{id}`: the profiles are merged into the configuration as it
    # is NOW, so a concurrent write of another section is not undone (S47-06).
    await session.execute(
        select(Project)
        .where(Project.id == ctx.project.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    from choregos_contracts import ModelProfile

    config = ProjectConfig.model_validate(ctx.project.config)
    config.models.profiles = {
        name: ModelProfile.model_validate(profile.model_dump()) for name, profile in body.profiles.items()
    }
    config.models.allow_unvalidated = body.allow_unvalidated

    rows = (
        (await session.execute(select(ModelProfileRow).where(ModelProfileRow.scope == "platform")))
        .scalars()
        .all()
    )
    validated = {row.litellm_model: list(row.validated_backends or []) for row in rows}
    from ..services.model_catalogue import load_policy

    # The organisation's catalogue refuses a profile mapped to a model that is not enabled (ADR 0060).
    policy = await load_policy(session, ctx.project.org_id)
    resolver = ModelResolver(
        gateway_url="", gateway_models=[], validated_backends=validated, catalogue=policy
    )
    backend = config.agent.default_backend
    from choregos_core import ModelResolutionError

    for name in body.profiles:
        try:
            resolver.resolve(f"profile:{name}", project=config, backend=backend)
        except ModelResolutionError as exc:
            raise unprocessable(str(exc), [{"loc": ["profiles", name], "msg": str(exc)}]) from exc

    ctx.project.config = config.model_dump(mode="json", exclude_none=True)
    await record(
        session,
        ctx.principal,
        "models.put",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
    )
    return await get_models(ctx)


@router.get("/projects/{id}/models/matrix", response_model=ModelMatrix, operation_id="getModelMatrix")
async def matrix(ctx: ProjectCtx, session: Db) -> ModelMatrix:
    """Matrice publiée par `EvalMatrix` : ce qui a été prouvé, pas ce qui est espéré."""
    rows = (await session.execute(select(ModelProfileRow))).scalars().all()
    entries: list[ModelMatrixEntry] = []
    generated_at = None
    for row in rows:
        for backend in row.validated_backends or []:
            entries.append(ModelMatrixEntry(backend=backend, model=row.litellm_model, validated=True))
        extra = (row.params or {}).get("matrix", [])
        for entry in extra:
            # A published cell carries more than the matrix shows (`cases`, `details`…): the
            # entry keeps its own fields, and a cell is never refused for what it adds.
            shown = {key: value for key, value in entry.items() if key in ModelMatrixEntry.model_fields}
            entries.append(ModelMatrixEntry.model_validate(shown))
        if (row.params or {}).get("matrix_generated_at"):
            generated_at = row.params["matrix_generated_at"]
    return ModelMatrix(generated_at=generated_at, entries=entries)

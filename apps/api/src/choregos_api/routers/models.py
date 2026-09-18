"""Modèles : catalogue plateforme, profils projet, matrice backend × modèle."""

from __future__ import annotations

from choregos_contracts import ProjectConfig
from choregos_core import ModelResolver
from choregos_core.models import PLATFORM_PROFILES
from fastapi import APIRouter
from sqlalchemy import select

from ..audit import record
from ..db.models import ModelProfileRow
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

    gateway = build("gateway", "litellm", {})
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
    config = ProjectConfig.model_validate(ctx.project.config)
    config.models.profiles = {
        name: profile.model_dump()  # type: ignore[misc]
        for name, profile in body.profiles.items()
    }
    config = ProjectConfig.model_validate(config.model_dump(mode="json"))
    config.models.allow_unvalidated = body.allow_unvalidated

    rows = (
        (await session.execute(select(ModelProfileRow).where(ModelProfileRow.scope == "platform")))
        .scalars()
        .all()
    )
    validated = {row.litellm_model: list(row.validated_backends or []) for row in rows}
    resolver = ModelResolver(gateway_url="", gateway_models=[], validated_backends=validated)
    backend = config.agent.default_backend
    from choregos_core import ModelResolutionError

    for name in body.profiles:
        try:
            resolver.resolve(f"profile:{name}", project=config, backend=backend)
        except ModelResolutionError as exc:
            raise unprocessable(str(exc), [{"loc": ["profiles", name], "msg": str(exc)}]) from exc

    ctx.project.config = config.model_dump(mode="json", exclude_none=True)
    await record(session, ctx.principal, "models.put", target_type="project", target_id=ctx.id)
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
            entries.append(ModelMatrixEntry.model_validate(entry))
        if (row.params or {}).get("matrix_generated_at"):
            generated_at = row.params["matrix_generated_at"]
    return ModelMatrix(generated_at=generated_at, entries=entries)

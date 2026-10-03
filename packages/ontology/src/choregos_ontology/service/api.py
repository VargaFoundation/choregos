# SPDX-License-Identifier: Apache-2.0
"""Les routes du greffon, montées par le cœur sous `/api/v1` (essai, éléments 1 et 2).

- `PUT /projects/{id}/ontology` : un paquet (fichiers YAML) validé, compilé, puis actif ;
- `GET /projects/{id}/ontology` : la version active ;
- `POST /projects/{id}/observations` : un rapport au format observations NDJSON v1, synchronisé
  vers les objets `finding` — tout ou rien : un rapport partiel n'écrit rien ;
- `GET /projects/{id}/objects/{type}` : les objets d'un type, vus avec l'habilitation par défaut.

Raccourcis de l'essai, à reprendre dans le socle : la synchronisation est une route et non l'action
système `connector.sync` (R-SOC-CON-04), et un paquet devient actif sans plan ni application en
deux temps (SOC-010). Les droits sont ceux du cœur : `project:write` pour écrire, `project:read`
pour lire.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path
from typing import Annotated, Any

from choregos_api.db.base import utcnow
from choregos_api.deps import Db, ProjectCtx
from choregos_api.errors import ApiError, conflict, unprocessable
from choregos_api.rbac import Permission
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from choregos_ontology.compiler import compile_directory
from choregos_ontology.observations import PartialReportError, parse_report, plan_sync
from choregos_ontology.service import objects
from choregos_ontology.service.objects import ToolRefusal
from choregos_ontology.service.store import ACTIVE, SUPERSEDED, ManagedObject, OntologyVersion

router = APIRouter(tags=["ontology"])

FINDING = "finding"
MAX_FILES = 300
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
_PATH = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*\.ya?ml$")


class OntologyPackage(BaseModel):
    """Les fichiers d'un paquet : chemin relatif (`objects/host.yaml`) → contenu YAML."""

    files: dict[str, str] = Field(min_length=1)


async def _serialize(session: AsyncSession, what: str, project_id: str) -> None:
    """Un seul écrivain à la fois par projet : deux synchronisations concurrentes calculeraient leur
    plan sur le même état et la seconde écraserait la première (PostgreSQL ; SQLite sérialise)."""
    if session.get_bind().dialect.name == "postgresql":
        verrou = text("SELECT pg_advisory_xact_lock(hashtext(:k))")
        await session.execute(verrou, {"k": f"{what}:{project_id}"})


def _write_package(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _check_package(package: OntologyPackage) -> None:
    if len(package.files) > MAX_FILES:
        raise unprocessable(f"{len(package.files)} files; at most {MAX_FILES}")
    if sum(len(c.encode()) for c in package.files.values()) > MAX_PACKAGE_BYTES:
        raise unprocessable(f"package larger than {MAX_PACKAGE_BYTES} bytes")
    refused = sorted(n for n in package.files if not _PATH.match(n) or ".." in n.split("/"))
    if refused:
        raise unprocessable(f"file names must be relative YAML paths without `..`: {refused}")


@router.put("/projects/{id}/ontology", operation_id="putOntology")
async def put_ontology(package: OntologyPackage, ctx: ProjectCtx, session: Db) -> dict[str, Any]:
    ctx.require(Permission.PROJECT_WRITE)
    _check_package(package)
    with tempfile.TemporaryDirectory(prefix="ontology-") as directory:
        root = Path(directory)
        await asyncio.to_thread(_write_package, root, package.files)
        compiled, validation = await asyncio.to_thread(compile_directory, root)
    if compiled is None:
        raise unprocessable(
            f"ontology_invalid: {len(validation.errors)} error(s)",
            errors=[issue.to_dict() for issue in validation.errors],
        )
    await _serialize(session, "ontology-version", ctx.project.id)
    await session.execute(
        update(OntologyVersion)
        .where(OntologyVersion.project_id == ctx.project.id, OntologyVersion.status == ACTIVE)
        .values(status=SUPERSEDED)
    )
    version = OntologyVersion(
        project_id=ctx.project.id,
        name=compiled.name,
        version=compiled.version,
        checksum=compiled.checksum,
        status=ACTIVE,
        compiled_ir=compiled.to_dict(),
        created_by=ctx.principal.email,
    )
    session.add(version)
    await session.flush()
    return {
        "id": version.id,
        "name": compiled.name,
        "version": compiled.version,
        "checksum": compiled.checksum,
        "mcp_tools": len(compiled.mcp_tools),
        "warnings": [issue.to_dict() for issue in validation.warnings],
    }


@router.get("/projects/{id}/ontology", operation_id="getOntology")
async def get_ontology(ctx: ProjectCtx, session: Db) -> dict[str, Any]:
    version = await objects.active_version(session, ctx.project.id)
    if version is None:
        raise ApiError(404, "Ontologie introuvable", "ce projet n'a pas d'ontologie active")
    ir = version.compiled_ir
    return {
        "id": version.id,
        "name": version.name,
        "version": version.version,
        "checksum": version.checksum,
        "created_at": version.created_at.isoformat(),
        "object_types": [o["name"] for o in ir.get("object_types", [])],
        "mcp_tools": [t["name"] for t in ir.get("mcp_tools", [])],
    }


@router.post("/projects/{id}/observations", operation_id="postObservations")
async def post_observations(request: Request, ctx: ProjectCtx, session: Db) -> dict[str, Any]:
    """Synchronise un rapport complet vers les objets `finding` ; un rapport partiel n'écrit rien."""
    ctx.require(Permission.PROJECT_WRITE)
    body = await request.body()
    if len(body) > MAX_REPORT_BYTES:
        raise ApiError(413, "Rapport trop gros", f"au plus {MAX_REPORT_BYTES} octets")
    version = await objects.active_version(session, ctx.project.id)
    if version is None:
        raise conflict("ce projet n'a pas d'ontologie active : PUT /projects/{id}/ontology d'abord")
    types = version.compiled_ir.get("object_types", [])
    finding_type = next((o for o in types if o["name"] == FINDING), None)
    if finding_type is None or (finding_type.get("datasource") or {}).get("type") != "table":
        raise conflict("l'ontologie active n'a pas de type `finding` à datasource `table`")
    try:
        observations = parse_report(body.decode("utf-8"))
    except (UnicodeDecodeError, PartialReportError) as error:
        raise unprocessable(
            f"observations_partial_read: {error}",
            errors=[{"code": "observations_partial_read", "message": str(error)}],
        ) from error
    await _serialize(session, "observations", ctx.project.id)
    rows = {
        row.id: row
        for row in (
            await session.execute(
                select(ManagedObject).where(
                    ManagedObject.project_id == ctx.project.id, ManagedObject.object_type == FINDING
                )
            )
        ).scalars()
    }
    current = {key: dict(row.properties or {}) for key, row in rows.items() if row.deleted_at is None}
    plan = plan_sync(current, observations, now=utcnow().isoformat())
    for key, properties in {**plan.create, **plan.update}.items():
        row = rows.get(key)
        if row is None:
            session.add(
                ManagedObject(project_id=ctx.project.id, object_type=FINDING, id=key, properties=properties)
            )
            continue
        row.properties = properties
        row.deleted_at = None
        row.row_version += 1
    await session.flush()
    return {"lines": len(observations), **plan.summary()}


@router.get("/projects/{id}/objects/{object_type}", operation_id="listObjects")
async def list_objects(
    object_type: str,
    ctx: ProjectCtx,
    session: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    version = await objects.active_version(session, ctx.project.id)
    if version is None:
        raise ApiError(404, "Ontologie introuvable", "ce projet n'a pas d'ontologie active")
    arguments: dict[str, Any] = {"limit": limit}
    if cursor:
        arguments["cursor"] = cursor
    try:
        return await objects.search(
            session, ctx.project.id, objects.object_type(version.compiled_ir, object_type), arguments
        )
    except ToolRefusal as refusal:
        raise ApiError(refusal.code, "Requête refusée", str(refusal)) from refusal

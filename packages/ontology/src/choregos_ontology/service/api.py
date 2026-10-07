# SPDX-License-Identifier: Apache-2.0
"""Les routes du greffon, montées par le cœur sous `/api/v1` (essai, éléments 1 et 2).

- `PUT /projects/{id}/ontology` : un paquet (fichiers YAML) validé, compilé, puis actif ;
- `GET /projects/{id}/ontology` : la version active ;
- `POST /projects/{id}/observations` : un rapport au format observations NDJSON v1, synchronisé
  vers les objets `finding` — tout ou rien : un rapport partiel n'écrit rien ;
- `GET /projects/{id}/objects/{type}` : les objets d'un type, vus avec l'habilitation par défaut ;
- `GET /projects/{id}/proposals[/{proposal_id}]` et `POST …/{proposal_id}/decision` : les
  propositions d'action — des actions du cœur depuis S20-08 — et leur décision, prise par le cœur.

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
from typing import Annotated, Any, Literal

import jsonschema
from choregos_api.db.base import utcnow
from choregos_api.deps import Db, ProjectCtx
from choregos_api.errors import ApiError, conflict, unprocessable
from choregos_api.rbac import Permission
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from choregos_ontology.compiler import compile_directory
from choregos_ontology.observations import PartialReportError, parse_report, plan_sync
from choregos_ontology.service import actions, objects
from choregos_ontology.service.objects import ToolRefusal
from choregos_ontology.service.store import ACTIVE, SUPERSEDED, ManagedObject, OntologyVersion
from choregos_ontology.validator import CORE_EFFECTS, Registry

router = APIRouter(tags=["ontology"])
#: Ce que l'installation sait faire : les effets du socle, et `gitops.pull_request` (AGT-014), que le
#: greffon exécute par l'adaptateur SCM du cœur.
REGISTRY = Registry(effects=CORE_EFFECTS | {"gitops.pull_request"})

FINDING = "finding"
MAX_FILES = 300
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
_PATH = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*\.ya?ml$")


class ProposalIn(BaseModel):
    action_type: str = Field(min_length=1, max_length=63)
    target: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    justification: str = Field(min_length=1, max_length=4000)


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


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


def _check_package(files: dict[str, str]) -> None:
    if len(files) > MAX_FILES:
        raise unprocessable(f"{len(files)} files; at most {MAX_FILES}")
    if sum(len(c.encode()) for c in files.values()) > MAX_PACKAGE_BYTES:
        raise unprocessable(f"package larger than {MAX_PACKAGE_BYTES} bytes")
    refused = sorted(n for n in files if not _PATH.match(n) or ".." in n.split("/"))
    if refused:
        raise unprocessable(f"file names must be relative YAML paths without `..`: {refused}")


@router.put("/projects/{id}/ontology", operation_id="putOntology")
async def put_ontology(package: OntologyPackage, ctx: ProjectCtx, session: Db) -> dict[str, Any]:
    ctx.require(Permission.PROJECT_WRITE)
    return await publier_l_ontologie(session, ctx.project.id, package.files, ctx.principal.email)


async def installer_depuis_un_gabarit(
    session: AsyncSession, projet: Any, fichiers: dict[str, str], auteur: str
) -> dict[str, Any]:
    """L'installateur `ontology` des gabarits (S20-09) : le paquet livré devient l'ontologie active du
    projet qui naît. Un paquet invalide fait échouer la naissance : c'est le gabarit qui est faux."""
    return await publier_l_ontologie(session, projet.id, fichiers, auteur)


async def publier_l_ontologie(
    session: AsyncSession, project_id: str, files: dict[str, str], auteur: str | None
) -> dict[str, Any]:
    """Un paquet validé, compilé, puis actif ; la version active passe `superseded`."""
    _check_package(files)
    with tempfile.TemporaryDirectory(prefix="ontology-") as directory:
        root = Path(directory)
        await asyncio.to_thread(_write_package, root, files)
        compiled, validation = await asyncio.to_thread(compile_directory, root, REGISTRY)
    if compiled is None:
        raise unprocessable(
            f"ontology_invalid: {len(validation.errors)} error(s)",
            errors=[issue.to_dict() for issue in validation.errors],
        )
    await _serialize(session, "ontology-version", project_id)
    await session.execute(
        update(OntologyVersion)
        .where(OntologyVersion.project_id == project_id, OntologyVersion.status == ACTIVE)
        .values(status=SUPERSEDED)
    )
    version = OntologyVersion(
        project_id=project_id,
        name=compiled.name,
        version=compiled.version,
        checksum=compiled.checksum,
        status=ACTIVE,
        compiled_ir=compiled.to_dict(),
        created_by=auteur,
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
        raise ApiError(404, "Ontology not found", "this project has no active ontology")
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
async def post_observations(request: Request, ctx: ProjectCtx, session: Db) -> Any:
    """Synchronise un rapport complet vers les objets `finding` ; un rapport partiel n'écrit rien."""
    ctx.require(Permission.PROJECT_WRITE)
    body = await request.body()
    if len(body) > MAX_REPORT_BYTES:
        raise ApiError(413, "Report too large", f"{MAX_REPORT_BYTES} bytes at most")
    version = await objects.active_version(session, ctx.project.id)
    if version is None:
        raise conflict("this project has no active ontology: PUT /projects/{id}/ontology first")
    types = version.compiled_ir.get("object_types", [])
    finding_type = next((o for o in types if o["name"] == FINDING), None)
    if finding_type is None or (finding_type.get("datasource") or {}).get("type") != "table":
        raise conflict("the active ontology has no `finding` type with a `table` datasource")
    try:
        observations = parse_report(body.decode("utf-8"))
    except (UnicodeDecodeError, PartialReportError) as error:
        # Aucun objet n'est écrit. Mais une preuve `collector.rerun` qui attendait CE rapport échoue :
        # un rapport partiel la rend impossible à collecter. Elle s'écrit, donc pas d'exception ici.
        await _serialize(session, "observations", ctx.project.id)
        tranchees = await actions.on_report(session, ctx.project, None, str(error))
        refus = unprocessable(
            f"observations_partial_read: {error}",
            errors=[{"code": "observations_partial_read", "message": str(error)}],
        )
        probleme = {**refus.to_problem(str(request.url.path)), "proposals_decided": tranchees}
        return JSONResponse(probleme, status_code=refus.status_code, media_type="application/problem+json")
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
    tranchees = await actions.on_report(session, ctx.project, observations, None)
    return {"lines": len(observations), **plan.summary(), "proposals_decided": tranchees}


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
        raise ApiError(404, "Ontology not found", "this project has no active ontology")
    arguments: dict[str, Any] = {"limit": limit}
    if cursor:
        arguments["cursor"] = cursor
    try:
        return await objects.search(
            session, ctx.project.id, objects.object_type(version.compiled_ir, object_type), arguments
        )
    except ToolRefusal as refusal:
        raise ApiError(refusal.code, "Request refused", str(refusal)) from refusal


# ───────────────────────────── propositions d'action ─────────────────────────────


async def _proposal(session: AsyncSession, project_id: str, proposal_id: str) -> Any:
    """Une proposition : une action du cœur née de l'ontologie (S20-08)."""
    try:
        return await actions.une_action(session, project_id, proposal_id)
    except actions.Refusal as refus:
        raise ApiError(404, "Proposal not found", f"proposal `{proposal_id}` does not exist") from refus


@router.get("/projects/{id}/proposals", operation_id="listProposals")
async def list_proposals(
    ctx: ProjectCtx,
    session: Db,
    status: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    return [
        actions.describe(p) for p in await actions.actions_de_l_ontologie(session, ctx.project.id, status)
    ]


@router.post("/projects/{id}/proposals", status_code=201, operation_id="createProposal")
async def create_proposal(body: ProposalIn, ctx: ProjectCtx, session: Db) -> Any:
    """Une proposition faite par un humain (ou un jeton d'API) : mêmes règles que celle d'un agent —
    paramètres, préconditions, chemins permis, politique — et le droit vient de ses rôles."""
    version = await objects.active_version(session, ctx.project.id)
    if version is None:
        raise conflict("this project has no active ontology: PUT /projects/{id}/ontology first")
    action = next(
        (a for a in version.compiled_ir.get("action_types", []) if a["name"] == body.action_type), None
    )
    if action is None:
        raise ApiError(404, "Action not found", f"action `{body.action_type}` does not exist")
    try:
        jsonschema.validate(body.params, action["parameters_schema"])
    except jsonschema.ValidationError as erreur:
        raise unprocessable(f"parameters refused: {erreur.message}") from erreur
    acteur = actions.Actor(kind="user", id=ctx.principal.email, user_id=ctx.principal.user_id)
    try:
        code, resultat = await actions.propose(
            session,
            ctx.project,
            version,
            body.action_type,
            body.model_dump(),
            acteur,
            ctx.principal,
            ctx.org_slug,
        )
    except actions.Refusal as refus:
        raise ApiError(refus.code, "Proposal refused", str(refus), errors=[refus.body]) from refus
    return JSONResponse(resultat, status_code=code)


@router.get("/projects/{id}/proposals/{proposal_id}", operation_id="getProposal")
async def get_proposal(proposal_id: str, ctx: ProjectCtx, session: Db) -> dict[str, Any]:
    return actions.describe(await _proposal(session, ctx.project.id, proposal_id))


@router.post("/projects/{id}/proposals/{proposal_id}/decision", operation_id="decideProposal")
async def decide_proposal(proposal_id: str, body: Decision, ctx: ProjectCtx, session: Db) -> Any:
    """La décision humaine, prise par le cœur (`decider`) — la même que
    `POST /projects/{id}/actions/{id}/decision`. `401` avec `reauth` : se ré-authentifier, recommencer."""
    proposal = await _proposal(session, ctx.project.id, proposal_id)
    return await actions.decide(
        session, ctx.project, ctx.org_slug, proposal, ctx.principal, body.decision, body.reason
    )

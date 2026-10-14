# SPDX-License-Identifier: Apache-2.0
"""The plugin's routes, mounted by the core under `/api/v1` (trial, items 1 and 2).

- `PUT /projects/{id}/ontology`: a package (YAML files) validated, compiled, then active;
- `GET /projects/{id}/ontology`: the active version;
- `POST /projects/{id}/observations`: a report in the observations NDJSON v1 format, synchronised to
  the `finding` objects — all or nothing: a partial report writes nothing;
- `GET /projects/{id}/objects/{type}`: the objects of a type, seen with the default clearance;
- `GET /projects/{id}/proposals[/{proposal_id}]` and `POST …/{proposal_id}/decision`: the action
  proposals — core actions since S20-08 — and their decision, taken by the core.

Trial shortcuts, to be taken up in the platform: synchronisation is a route and not the system action
`connector.sync` (R-SOC-CON-04), and a package becomes active without a two-step plan and apply
(SOC-010). The rights are the core's: `project:write` to write, `project:read` to read.
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
#: What the installation can do: the platform's effects, and `gitops.pull_request` (AGT-014), which the
#: plugin executes through the core's SCM adapter.
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
    """A package's files: relative path (`objects/host.yaml`) → YAML content."""

    files: dict[str, str] = Field(min_length=1)


async def _serialize(session: AsyncSession, what: str, project_id: str) -> None:
    """One writer at a time per project: two concurrent synchronisations would compute their plan on
    the same state and the second would overwrite the first (PostgreSQL; SQLite serialises)."""
    if session.get_bind().dialect.name == "postgresql":
        lock = text("SELECT pg_advisory_xact_lock(hashtext(:k))")
        await session.execute(lock, {"k": f"{what}:{project_id}"})


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
    return await publish_ontology(session, ctx.project.id, package.files, ctx.principal.email)


async def install_from_template(
    session: AsyncSession, project: Any, files: dict[str, str], author: str
) -> dict[str, Any]:
    """The templates' `ontology` installer (S20-09): the shipped package becomes the active ontology of
    the project being born. An invalid package fails the birth: the template is wrong."""
    return await publish_ontology(session, project.id, files, author)


async def publish_ontology(
    session: AsyncSession, project_id: str, files: dict[str, str], author: str | None
) -> dict[str, Any]:
    """A package validated, compiled, then active; the active version becomes `superseded`."""
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
        created_by=author,
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
    """Synchronise a complete report to the `finding` objects; a partial report writes nothing."""
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
        # No object is written. But a `collector.rerun` evidence that waited for THIS report fails: a
        # partial report makes it impossible to collect. It is written, hence no exception here.
        await _serialize(session, "observations", ctx.project.id)
        settled = await actions.on_report(session, ctx.project, None, str(error))
        refusal = unprocessable(
            f"observations_partial_read: {error}",
            errors=[{"code": "observations_partial_read", "message": str(error)}],
        )
        problem = {**refusal.to_problem(str(request.url.path)), "proposals_decided": settled}
        return JSONResponse(problem, status_code=refusal.status_code, media_type="application/problem+json")
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
    settled = await actions.on_report(session, ctx.project, observations, None)
    return {"lines": len(observations), **plan.summary(), "proposals_decided": settled}


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


# ───────────────────────────── action proposals ─────────────────────────────


async def _proposal(session: AsyncSession, project_id: str, proposal_id: str) -> Any:
    """A proposal: a core action born from the ontology (S20-08)."""
    try:
        return await actions.get_action(session, project_id, proposal_id)
    except actions.Refusal as refusal:
        raise ApiError(404, "Proposal not found", f"proposal `{proposal_id}` does not exist") from refusal


@router.get("/projects/{id}/proposals", operation_id="listProposals")
async def list_proposals(
    ctx: ProjectCtx,
    session: Db,
    status: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    return [actions.describe(p) for p in await actions.ontology_actions(session, ctx.project.id, status)]


@router.post("/projects/{id}/proposals", status_code=201, operation_id="createProposal")
async def create_proposal(body: ProposalIn, ctx: ProjectCtx, session: Db) -> Any:
    """A proposal made by a person (or an API token): the same rules as an agent's — parameters,
    preconditions, allowed paths, policy — and the right comes from their roles."""
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
    except jsonschema.ValidationError as error:
        raise unprocessable(f"parameters refused: {error.message}") from error
    actor = actions.Actor(kind="user", id=ctx.principal.email, user_id=ctx.principal.user_id)
    try:
        code, result = await actions.propose(
            session,
            ctx.project,
            version,
            body.action_type,
            body.model_dump(),
            actor,
            ctx.principal,
            ctx.org_slug,
        )
    except actions.Refusal as refusal:
        raise ApiError(refusal.code, "Proposal refused", str(refusal), errors=[refusal.body]) from refusal
    return JSONResponse(result, status_code=code)


@router.get("/projects/{id}/proposals/{proposal_id}", operation_id="getProposal")
async def get_proposal(proposal_id: str, ctx: ProjectCtx, session: Db) -> dict[str, Any]:
    return actions.describe(await _proposal(session, ctx.project.id, proposal_id))


@router.post("/projects/{id}/proposals/{proposal_id}/decision", operation_id="decideProposal")
async def decide_proposal(proposal_id: str, body: Decision, ctx: ProjectCtx, session: Db) -> Any:
    """The human decision, taken by the core (`decider`) — the same as
    `POST /projects/{id}/actions/{id}/decision`. `401` with `reauth`: authenticate again, then retry."""
    proposal = await _proposal(session, ctx.project.id, proposal_id)
    return await actions.decide(
        session, ctx.project, ctx.org_slug, proposal, ctx.principal, body.decision, body.reason
    )

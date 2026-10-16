# SPDX-License-Identifier: Apache-2.0
"""`GET /projects/{id}/readiness`: what a project still lacks to run (S40-06).

Computed from stored state by `services/readiness.py`: no network call, so the settings page, the
overview, the CLI and the MCP door can all read it cheaply and show the same list.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import Db, ProjectCtx
from ..schemas.readiness import ProjectReadiness
from ..services.readiness import project_readiness

router = APIRouter(tags=["projects"])


@router.get("/projects/{id}/readiness", response_model=ProjectReadiness, operation_id="getProjectReadiness")
async def get_project_readiness(ctx: ProjectCtx, session: Db) -> ProjectReadiness:
    return await project_readiness(session, ctx.project, ctx.org_slug)

# SPDX-License-Identifier: Apache-2.0
"""`GET /projects/{id}/readiness`: what a project still lacks to run (S40-06).

S40-06a declares the contract: the operation answers 501 until S40-06b computes the list. The stub
exists because the contract test requires the application to serve exactly the paths and operation
ids the OpenAPI declares.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

router = APIRouter(tags=["projects"])


@router.get("/projects/{id}/readiness", operation_id="getProjectReadiness")
async def get_project_readiness(id: str) -> Any:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="project readiness arrives with S40-06b"
    )

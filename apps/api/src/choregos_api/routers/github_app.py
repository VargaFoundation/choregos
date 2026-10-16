# SPDX-License-Identifier: Apache-2.0
"""GitHub App installations: discovered, installed from the console, claimed (S40-04, ADR 0073).

S40-04a declares the contract: every operation answers 501 until S40-04b implements it. The stubs
exist because the contract test requires the application to serve exactly the paths and operation
ids the OpenAPI declares.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

router = APIRouter(tags=["connectors"])

_NOT_IMPLEMENTED = "GitHub App installations arrive with S40-04b (ADR 0073)"


def _stub() -> Any:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED)


@router.get("/orgs/{org}/github", operation_id="getGitHubAppStatus")
async def get_github_app_status(org: str) -> Any:
    return _stub()


@router.post("/orgs/{org}/github/refresh", operation_id="refreshGitHubInstallations")
async def refresh_github_installations(org: str) -> Any:
    return _stub()


@router.get("/orgs/{org}/github/repositories", operation_id="listGitHubRepositories")
async def list_github_repositories(
    org: str, q: str | None = None, installation: int | None = None, page: int = 1
) -> Any:
    return _stub()


@router.post("/orgs/{org}/github/install-link", operation_id="createGitHubInstallLink")
async def create_github_install_link(org: str) -> Any:
    return _stub()


@router.get("/github/setup", operation_id="githubAppSetup")
async def github_app_setup(
    installation_id: int, state: str, setup_action: str | None = None, code: str | None = None
) -> Any:
    return _stub()

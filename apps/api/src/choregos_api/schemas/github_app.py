# SPDX-License-Identifier: Apache-2.0
"""GitHub App installations: the DTOs of `openapi.yaml` (S40-04, ADR 0073)."""

from __future__ import annotations

from typing import Literal

from .base import Dto, PageMeta


class GitHubInstallationDto(Dto):
    id: int
    account_login: str
    account_type: Literal["Organization", "User"]
    repository_selection: Literal["all", "selected"]
    suspended: bool
    claimed: bool
    settings_url: str


class GitHubAppStatus(Dto):
    configured: bool
    app_slug: str | None = None
    app_name: str | None = None
    install_url: str | None = None
    multi_org_claim: Literal["implicit", "oauth", "unavailable"]
    installations: list[GitHubInstallationDto]


class GitHubRepository(Dto):
    full_name: str
    private: bool
    default_branch: str
    installation_id: int


class GitHubRepositoryPage(Dto):
    items: list[GitHubRepository]
    meta: PageMeta


class GitHubInstallLinkRequest(Dto):
    project: str | None = None
    return_to: str


class GitHubInstallLink(Dto):
    url: str

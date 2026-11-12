# SPDX-License-Identifier: Apache-2.0
"""GitHub App installations: the DTOs of `openapi.yaml` (S40-04, ADR 0073)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

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


# ───────────────── the deployment's App, registered from a manifest (S67-31, ADR 0161) ─────────────────


class GitHubAppRegistrationStatus(Dto):
    """Which App the instance acts as, where it comes from, and what a new one would be asked for."""

    #: `deployment`: the deployment's settings name it; `manifest`: registered from the console;
    #: `none`: no App.
    source: Literal["none", "deployment", "manifest"]
    app_id: str | None = None
    slug: str | None = None
    name: str | None = None
    owner_login: str | None = None
    owner_type: str | None = None
    html_url: str | None = None
    public: bool | None = None
    webhook_active: bool | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    #: The stored App opens under this deployment's key (false: the key that sealed it is gone).
    usable: bool
    #: Why a new App cannot be created from the console now, or null when it can.
    blocked_by: str | None = None
    suggested_name: str
    permissions: dict[str, Literal["read", "write"]]
    events: list[str]
    webhook_url: str
    callback_url: str
    redirect_url: str


class GitHubAppManifestRequest(Dto):
    #: The App's name on GitHub: unique across GitHub, 34 characters at most.
    name: str = Field(min_length=1, max_length=34)
    #: The GitHub organisation that will own the App; absent: the personal account of the person
    #: who registers it on github.com.
    organization: str | None = Field(default=None, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
    #: Installable by any account (true) or only by the owner's account (false). No default: a choice.
    public: bool
    #: Whether GitHub can reach this instance's webhook URL and should deliver events.
    webhook_active: bool = True


class GitHubAppManifestStart(Dto):
    """What the console POSTs to GitHub: the form's `action` and its one field, `manifest`."""

    action_url: str
    manifest: dict[str, Any]

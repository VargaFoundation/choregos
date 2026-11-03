# SPDX-License-Identifier: Apache-2.0
"""GitHub access shared by the tracker and the SCM: App, installation tokens, REST/GraphQL client."""

from __future__ import annotations

from .app import GitHubApp, GitHubAppPort
from .auth import (
    APP_EVENTS,
    APP_PERMISSIONS,
    DEFAULT_PERMISSIONS,
    RUNNER_PERMISSIONS,
    GitHubAppAuth,
    InstallationToken,
)
from .client import GitHubClient

__all__ = [
    "APP_EVENTS",
    "APP_PERMISSIONS",
    "DEFAULT_PERMISSIONS",
    "RUNNER_PERMISSIONS",
    "GitHubApp",
    "GitHubAppAuth",
    "GitHubAppPort",
    "GitHubClient",
    "InstallationToken",
]

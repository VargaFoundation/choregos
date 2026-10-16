# SPDX-License-Identifier: Apache-2.0
"""Accès GitHub partagé par le tracker et le SCM : App, jetons d'installation, client REST/GraphQL."""

from __future__ import annotations

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
    "GitHubAppAuth",
    "GitHubClient",
    "InstallationToken",
]

"""Accès GitHub partagé par le tracker et le SCM : App, jetons d'installation, client REST/GraphQL."""

from __future__ import annotations

from .auth import DEFAULT_PERMISSIONS, RUNNER_PERMISSIONS, GitHubAppAuth, InstallationToken
from .client import GitHubClient

__all__ = [
    "DEFAULT_PERMISSIONS",
    "RUNNER_PERMISSIONS",
    "GitHubAppAuth",
    "GitHubClient",
    "InstallationToken",
]

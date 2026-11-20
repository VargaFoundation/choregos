# SPDX-License-Identifier: Apache-2.0
"""Source control adapters."""

from __future__ import annotations

from .azure_repos import AzureReposScm
from .github import GitHubScm

__all__ = ["AzureReposScm", "GitHubScm"]

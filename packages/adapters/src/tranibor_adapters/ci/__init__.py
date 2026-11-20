# SPDX-License-Identifier: Apache-2.0
"""Continuous integration adapters."""

from __future__ import annotations

from .azure_pipelines import AzurePipelinesCi
from .github_actions import GitHubActionsCi
from .gitlab import GitLabCi
from .jenkins import JenkinsCi
from .tekton import TektonCi

__all__ = ["AzurePipelinesCi", "GitHubActionsCi", "GitLabCi", "JenkinsCi", "TektonCi"]

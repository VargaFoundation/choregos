# SPDX-License-Identifier: Apache-2.0
"""Continuous integration adapters."""

from __future__ import annotations

from .azure_pipelines import AzurePipelinesCi
from .gitlab import GitLabCi
from .tekton import TektonCi

__all__ = ["AzurePipelinesCi", "GitLabCi", "TektonCi"]

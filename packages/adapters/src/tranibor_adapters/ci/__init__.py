# SPDX-License-Identifier: Apache-2.0
"""Continuous integration adapters."""

from __future__ import annotations

from .gitlab import GitLabCi
from .tekton import TektonCi

__all__ = ["GitLabCi", "TektonCi"]

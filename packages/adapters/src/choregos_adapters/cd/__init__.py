# SPDX-License-Identifier: Apache-2.0
"""Continuous deployment adapters."""

from __future__ import annotations

from .argocd import ArgoCdAdapter

__all__ = ["ArgoCdAdapter"]

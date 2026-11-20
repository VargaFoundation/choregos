# SPDX-License-Identifier: Apache-2.0
"""Tranibor orchestrator: Temporal workflows and activities.

The orchestrator plans and never calls a model: it decides which stage to launch, and the runner
runs it. All the decision logic comes from `tranibor_core`.
"""

from __future__ import annotations

import tranibor_contracts  # noqa: F401 - adopts the former environment variable names, first (ADR 0176 R4)

__version__ = "0.1.0"

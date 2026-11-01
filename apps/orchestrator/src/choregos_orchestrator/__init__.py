# SPDX-License-Identifier: Apache-2.0
"""Choregos orchestrator: Temporal workflows and activities.

The orchestrator plans and never calls a model: it decides which stage to launch, and the runner
runs it. All the decision logic comes from `choregos_core`.
"""

from __future__ import annotations

__version__ = "0.1.0"

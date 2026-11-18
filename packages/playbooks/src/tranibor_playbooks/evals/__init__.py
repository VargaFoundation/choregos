# SPDX-License-Identifier: Apache-2.0
"""Playbook evals: deterministic assertions, plus a judge for the rest.

A degraded prompt must fail CI. The cases live in `cases/`, each one describing
what it expects of the rendering (and, in full mode, of a real agent's behaviour).
"""

from __future__ import annotations

from pathlib import Path

CASES_DIR = Path(__file__).parent / "cases"

__all__ = ["CASES_DIR"]

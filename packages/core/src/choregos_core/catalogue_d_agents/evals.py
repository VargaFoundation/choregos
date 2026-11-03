# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:external — former module of the agent evals, removed in 0.22.0 (ADR 0098)
"""The former name of `choregos_core.agent_catalogue.evals`: `python -m` on it still runs the evals."""

from __future__ import annotations

from choregos_core.agent_catalogue.evals import AgentEvalResult, cases, evaluate, main

__all__ = ["AgentEvalResult", "cases", "evaluate", "main"]

if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: Apache-2.0
"""Telling a gateway budget refusal from any other silent agent (S27-01, ADR 0047).

The run's virtual key is the hard cap: when it is spent, the gateway refuses every further model
call. The agent then either ends its turn with an error (a JSON-RPC error on `session/prompt`),
writes the provider's message to its error output, or relays it as text. Before this module the
runner reported all three as `agent_silencieux` or `invalid_result` — reasons the workflow
*retries*, with a fresh key at the full stage budget. A refusal is not a failure to retry: it is
the budget doing its job, and the stage stops with `reason="budget"`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: What the gateway says when a key, a team or a user is over its budget. LiteLLM raises
#: `BudgetExceededError` ("Budget has been exceeded! Current cost: …, Max budget: …") and its proxy
#: answers with the error type `budget_exceeded`. These are the gateway's words, not an agent's: a
#: business agent writing "the budget was exceeded" about an invoice must not stop its own run.
GATEWAY_SIGNATURES = re.compile(
    r"budget_exceeded|BudgetExceededError|ExceededBudget|budget has been exceeded",
    re.IGNORECASE,
)

#: The reason a stage result carries when the gateway refused its key for budget.
BUDGET_REASON = "budget"


def gateway_budget_refusal(*sources: Iterable[str]) -> str | None:
    """The first line that carries a gateway budget refusal, or `None`.

    `sources` are the agent's prompt errors, its error output and the text it said — read in that
    order, the most reliable first.
    """
    for source in sources:
        for line in source:
            if line and GATEWAY_SIGNATURES.search(line):
                return line.strip()[:300]
    return None

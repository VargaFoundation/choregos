# SPDX-License-Identifier: Apache-2.0
"""The "direct" gateway: there is none.

Choregos' default model is a gateway that mints **one virtual key per run**, capped, and that
counts the spend at the source. Two situations do not fit this mould:

- the agent brings its own credentials (a Claude Code subscription, for example): the platform
  then has no key to issue, and above all must not write an empty one;
- a trial deployment talks to a provider with a single key, set in the pod.

What is lost, and must be known: **no spend ceiling, no measured cost**. A step's budget is then
limited to what the orchestrator can still hold: turns and minutes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from choregos_core.domain import GatewayModel, Spend, VirtualKey


class DirectGateway:
    """Returns the key it was given, or nothing at all."""

    #: Nothing is discovered, capped or measured: the platform's own reservation is all there is.
    capabilities: frozenset[str] = frozenset()

    def __init__(self, key: str = "", *, models: list[str] | None = None) -> None:
        self.key = key
        self.models = list(models or [])

    async def ensure_team(self, team_id: str, budget_usd: float, duration: str = "1d") -> str:
        """No gateway, no team: the project's day is enforced by the platform's own reservation."""
        return team_id

    async def mint_key(
        self,
        metadata: dict[str, Any],
        budget_usd: float,
        ttl_s: int,
        models: list[str],
        team_id: str | None = None,
    ) -> VirtualKey:
        # The identifier must be UNIQUE per run: the platform stores each key in a table where
        # `key_id` carries a unique index. A constant identifier passed on the first run and made
        # the second fail on a constraint violation, a message that talks about the database where
        # the culprit is the connector.
        run = str(metadata.get("run_id") or uuid4())
        return VirtualKey(
            key=self.key,
            key_id=f"direct:{run}",
            budget_usd=budget_usd,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
            models=models,
            metadata=dict(metadata),
        )

    async def spend(self, key_id: str) -> Spend:
        # Zero is not "free": it is "not measured". The cost table will show it at zero, which is
        # the accepted consequence of doing without a gateway.
        return Spend()

    async def revoke(self, key_id: str) -> None:
        return None

    async def list_models(self) -> list[GatewayModel]:
        return [GatewayModel(model_name=name, litellm_model=name) for name in self.models]

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "models": len(self.models)}

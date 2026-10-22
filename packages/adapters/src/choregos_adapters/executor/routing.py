# SPDX-License-Identifier: Apache-2.0
"""A step with its own image runs on a second executor (ADR 0072, S40-03c).

`agent_sandbox` makes every sandbox from one image: the warm pool's. A step that names its own image
(a `job` with `image:`, ADR 0056; a code agent in an operator-named image), or a hosted agent's
container beside the runner (ADR 0080), cannot be honoured there, and the executor refuses it. On a
deployment whose default executor is `agent_sandbox` that would make those steps impossible, so
`RoutingExecutor` sends them to a second executor (`k8s_job` by default).

The choice is made once, at `start`, on `spec.runner_image` and `spec.agent_container`. Everything
after is routed by `ref.kind`, which the orchestrator persists per run (`runs.executor_kind`):
status, logs, cancel, renew, suspend and resume reach the executor that started the run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from choregos_core import ExecRef, ExecStatus, StageJobSpec

from ..base import Executor


class RoutingExecutor:
    def __init__(self, primary: Executor, secondary: Executor, pool_image: str) -> None:
        self.primary = primary
        self.secondary = secondary
        self.pool_image = pool_image
        #: What the default executor can do. A run on the secondary may do less: ask `capabilities_for`.
        self.capabilities: frozenset[str] = frozenset(primary.capabilities)

    @property
    def kind(self) -> Any:
        return getattr(self.primary, "kind", None)

    def _is_custom(self, spec: StageJobSpec) -> bool:
        # A hosted agent's container beside the runner (ADR 0080): no pool sandbox has one.
        if spec.agent_container is not None:
            return True
        return bool(spec.runner_image) and spec.runner_image != self.pool_image

    def _delegate(self, ref: ExecRef) -> Executor:
        return self.secondary if str(ref.kind) == str(getattr(self.secondary, "kind", "")) else self.primary

    def capabilities_for(self, ref: ExecRef) -> frozenset[str]:
        """What the executor that runs THIS run can do."""
        return frozenset(self._delegate(ref).capabilities)

    async def start(self, spec: StageJobSpec) -> ExecRef:
        return await (self.secondary if self._is_custom(spec) else self.primary).start(spec)

    async def status(self, ref: ExecRef) -> ExecStatus:
        return await self._delegate(ref).status(ref)

    def logs(self, ref: ExecRef) -> AsyncIterator[str]:
        return self._delegate(ref).logs(ref)

    async def cancel(self, ref: ExecRef) -> None:
        await self._delegate(ref).cancel(ref)

    async def renew(self, ref: ExecRef, run_token: str, *, gateway_key: str | None = None) -> None:
        renew = getattr(self._delegate(ref), "renew", None)
        if renew is not None:
            await renew(ref, run_token, gateway_key=gateway_key)

    async def suspend(self, ref: ExecRef, until: datetime) -> bool:
        suspend = getattr(self._delegate(ref), "suspend", None)
        return bool(await suspend(ref, until)) if suspend is not None else False

    async def resume(
        self, ref: ExecRef, run_token: str, until: datetime, *, gateway_key: str | None = None
    ) -> bool:
        resume = getattr(self._delegate(ref), "resume", None)
        if resume is None:
            return False
        return bool(await resume(ref, run_token, until, gateway_key=gateway_key))

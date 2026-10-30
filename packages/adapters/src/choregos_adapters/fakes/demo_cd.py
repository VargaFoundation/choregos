# SPDX-License-Identifier: Apache-2.0
"""A demo CD (S21-24): what the train of a demo tenant promotes, without Argo CD.

The dev tenant's development project (S21-25) takes a REAL train: promotion, soak, verification,
ticket notification (ADR 0041). It needs a `cd` that answers. The `FakeCd` fake lives in the
process that built it — the orchestrator would promote an application that the API would not see.
The `cd: demo` type therefore holds the same state as the business families:

- without a `url`, in memory, shared in the process;
- with a `url`, served by the fakes' process (`fakes/server.py`, `/cd/mcp`): the API and the
  orchestrator see the same environment.

Refused in staging and in prod (like the business families): a demonstration, not a deployment.
Every write is idempotent — a Temporal activity is replayed (ADR 0008).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from choregos_core.domain import Change, Health, PromotionRef, RolloutState, Window

from ..registry import OperationSpec, schema_d_entree
from .hr import BusinessFake

_OBJECT_LIST: dict[str, Any] = {"type": "array", "items": {"type": "object"}}

DEMO_CD_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "promote",
        "write",
        "promotes a release's changes to an environment (the same release twice: done)",
        schema_d_entree("env", "release", changes=_OBJECT_LIST),
    ),
    OperationSpec("health", "read", "reads an application's health and revision", schema_d_entree("app")),
    OperationSpec("rollout_status", "read", "reads an application's rollout", schema_d_entree("app")),
    OperationSpec("abort_rollout", "write", "aborts an application's rollout", schema_d_entree("app")),
    OperationSpec("current_revision", "read", "reads an application's revision", schema_d_entree("app")),
    OperationSpec(
        "set_sync_window",
        "write",
        "sets an application's sync windows",
        schema_d_entree("app", windows=_OBJECT_LIST),
    ),
    OperationSpec(
        "break_next_rollout",
        "write",
        "makes the next rollout analysis fail, to show a rollback",
        schema_d_entree(),
    ),
)


@dataclass
class FakeDemoCd(BusinessFake):
    """A demo environment: a promoted application is healthy, at the promoted revision."""

    operations = DEMO_CD_OPERATIONS
    applications: dict[str, dict[str, Any]] = field(default_factory=dict)
    promotions: list[dict[str, Any]] = field(default_factory=list)
    windows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    break_next: bool = False

    def promote(self, env: str, release: str, changes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        for done in self.promotions:
            if (done["env"], done["release"]) == (env, release):
                return {**done, "promoted": False}
        promotion = {
            "env": env,
            "release": release,
            "number": len(self.promotions) + 1,
            "apps": [str(c["app"]) for c in changes or []],
        }
        # A promotion broken on purpose (`break_next_rollout`) arrives degraded: the train sees it at
        # its analysis, and rolls back. A read changes nothing; only writes write.
        status = "Degraded" if self.break_next else "Healthy"
        self.break_next = False
        for change in changes or []:
            revision = str(change.get("tag") or change.get("image") or release)
            self.applications[str(change["app"])] = {
                "status": status,
                "revision": revision,
                "rollout": status,
            }
        self.promotions.append(promotion)
        return {**promotion, "promoted": True}

    def health(self, app: str) -> dict[str, Any]:
        state = self.applications.get(app)
        if state is None:
            return {"status": "Missing", "message": f"{app} has never been promoted here"}
        return {"status": state["status"], "revision": state["revision"]}

    def rollout_status(self, app: str) -> dict[str, Any]:
        state = self.applications.get(app)
        if state is None:
            return {"phase": "Unknown", "message": f"{app} has never been promoted here"}
        message = "demo: analysis failed on purpose" if state["rollout"] == "Degraded" else ""
        return {
            "phase": state["rollout"],
            "current_step": 1,
            "total_steps": 1,
            "canary_weight": 100,
            "message": message,
        }

    def abort_rollout(self, app: str) -> dict[str, Any]:
        state = self.applications.get(app)
        if state is not None:
            state["rollout"] = "Aborted"
        return {"app": app, "phase": "Aborted"}

    def current_revision(self, app: str) -> dict[str, Any]:
        return {"app": app, "revision": (self.applications.get(app) or {}).get("revision") or "unknown"}

    def set_sync_window(self, app: str, windows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        self.windows[app] = list(windows or [])
        return {"app": app, "windows": len(self.windows[app])}

    def break_next_rollout(self) -> dict[str, Any]:
        self.break_next = True
        return {"armed": True}


@dataclass
class DemoCd:
    """The `cd` adapter (the train's protocol) on a desk: local (`Desk`) or served (`RemoteDesk`).
    Domain objects go through as JSON, and come back."""

    desk: Any

    async def current_revision(self, app: str) -> str:
        return str((await self.desk.execute("current_revision", {"app": app}))["revision"])

    async def promote(self, env: str, changes: list[Change], release: str) -> PromotionRef:
        payload = [c.model_dump(mode="json", exclude_none=True) for c in changes]
        done = await self.desk.execute("promote", {"env": env, "release": release, "changes": payload})
        return PromotionRef(kind="commit", url=f"demo://{env}/{done['number']}", ref=release, merged=True)

    async def health(self, app: str) -> Health:
        return Health.model_validate(await self.desk.execute("health", {"app": app}))

    async def rollout_status(self, app: str) -> RolloutState:
        return RolloutState.model_validate(await self.desk.execute("rollout_status", {"app": app}))

    async def abort_rollout(self, app: str) -> None:
        await self.desk.execute("abort_rollout", {"app": app})

    async def set_sync_window(self, app: str, windows: list[Window]) -> None:
        payload = [w.model_dump(mode="json") for w in windows]
        await self.desk.execute("set_sync_window", {"app": app, "windows": payload})

    async def test(self) -> dict[str, Any]:
        result: dict[str, Any] = await self.desk.test()
        return result


__all__ = ["DEMO_CD_OPERATIONS", "DemoCd", "FakeDemoCd"]

# Former names that other code imports (ADR 0098 §3 (b)); `fakes/cd_de_demo.py` keeps the former path.
OPERATIONS_CD_DE_DEMO = DEMO_CD_OPERATIONS  # lang: fr-ok alias:internal
CdDeDemo = DemoCd  # lang: fr-ok alias:internal
FakeCdDeDemo = FakeDemoCd  # lang: fr-ok alias:internal

"""`ReleaseTrain` : un singleton par projet × environnement (docs/plan/05 §5.2).

Le train est le deuxième des trois verrous : la merge queue garde `main` vert, le train
garde l'environnement, les garde-fous déclaratifs (fenêtres Argo, Environments GitHub)
tiennent même si Choregos tombe.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from choregos_contracts import ReleaseStatus

    from ..activities import train as train_activities

DEFAULT_RETRY = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=2))
NO_RETRY = RetryPolicy(maximum_attempts=1)


@dataclass
class TrainInput:
    project_slug: str
    env: str
    project_id: str | None = None
    carried_items: list[dict[str, Any]] = field(default_factory=list)
    batch_no: int = 1
    frozen: bool = False


@workflow.defn(name="ReleaseTrain", sandboxed=False)
class ReleaseTrain:
    """Collecte, départ, staging, soak, approbation, canary, vérification, rollback, gel."""

    def __init__(self) -> None:
        self.batch: list[dict[str, Any]] = []
        self.events: deque[dict[str, Any]] = deque()
        self.status: str = str(ReleaseStatus.COLLECTING)
        self.frozen: bool = False
        self.freeze_reason: str | None = None
        self.depart_requested: bool = False
        self.express: bool = False
        self.approved: bool | None = None
        self.approved_by: str | None = None
        self.abort_requested: bool = False
        self.batch_no: int = 1
        self.current_release: str | None = None
        self.next_departure: str | None = None
        self.window_open: bool = True

    # ───────────────────────── signaux ─────────────────────────

    @workflow.signal
    def merged(self, payload: dict[str, Any]) -> None:
        """Un ticket fusionné monte dans le train. Un doublon ne compte qu'une fois."""
        if any(item.get("work_item_key") == payload.get("work_item_key") for item in self.batch):
            return
        self.batch.append(payload)
        labels = payload.get("labels") or []
        if "hotfix" in labels:
            self.express = True
            self.depart_requested = True

    @workflow.signal
    def depart_now(self, payload: dict[str, Any]) -> None:
        self.depart_requested = True

    @workflow.signal
    def freeze(self, payload: dict[str, Any]) -> None:
        self.frozen = True
        self.freeze_reason = str(payload.get("reason", ""))

    @workflow.signal
    def unfreeze(self, payload: dict[str, Any]) -> None:
        self.frozen = False
        self.freeze_reason = None

    @workflow.signal
    def approve(self, payload: dict[str, Any]) -> None:
        self.approved = True
        self.approved_by = str(payload.get("by", ""))

    @workflow.signal
    def reject(self, payload: dict[str, Any]) -> None:
        self.approved = False
        self.approved_by = str(payload.get("by", ""))

    @workflow.signal
    def abort(self, payload: dict[str, Any]) -> None:
        self.abort_requested = True

    @workflow.signal
    def deploy_event(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)

    @workflow.query
    def status_query(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "batch_size": len(self.batch),
            "pending_items": [item.get("work_item_key", "") for item in self.batch],
            "frozen": self.frozen,
            "freeze_reason": self.freeze_reason,
            "next_departure": self.next_departure,
            "window_open": self.window_open,
            "release_id": self.current_release,
        }

    # ───────────────────────── boucle ─────────────────────────

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = TrainInput(**payload)
        self.batch = list(params.carried_items)
        self.batch_no = params.batch_no
        self.frozen = params.frozen

        config = await workflow.execute_activity(
            train_activities.load_train_config,
            {"project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        if config.get("mode") == "auto_sync":
            # Pas de train : Argo suit `main`. Le workflow reste vivant pour les requêtes.
            self.status = "auto_sync"
            await _wait(lambda: self.abort_requested)
            return {"status": "auto_sync"}

        departures = 0
        while departures < 20 and not self.abort_requested:
            await self._collect(config)
            if self.abort_requested:
                break
            released = await self._depart(params, config)
            departures += 1
            if released.get("frozen"):
                self.frozen = True
                self.freeze_reason = released.get("reason", "rollback")
            if workflow.info().get_current_history_length() > 15_000:
                workflow.continue_as_new(
                    {
                        **payload,
                        "carried_items": self.batch,
                        "batch_no": self.batch_no,
                        "frozen": self.frozen,
                    }
                )
        return {"status": self.status, "batches": departures}

    async def _collect(self, config: dict[str, Any]) -> None:
        """Attend le cron, le lot plein, un départ manuel ou un hotfix."""
        self.status = str(ReleaseStatus.COLLECTING)
        batch_max = int(config.get("batch_max", 8))
        while True:
            window = await workflow.execute_activity(
                train_activities.check_window,
                {"config": config},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
            self.window_open = bool(window.get("open", True))
            self.next_departure = window.get("next_departure")
            wait_s = float(window.get("wait_seconds", 300))
            ready = len(self.batch) >= batch_max or (
                self.batch and self.window_open and window.get("due", False)
            )
            # Un départ demandé par un humain (ou un hotfix) passe outre la fenêtre et le cron :
            # c'est un acte délibéré, tracé dans l'audit. Seul le gel le retient.
            if (self.express or self.depart_requested) and self.batch and not self.frozen:
                return
            if ready and not self.frozen and len(self.batch) >= int(config.get("batch_min", 1)):
                return
            await _wait(
                lambda: self.depart_requested or self.express or self.abort_requested,
                timedelta(seconds=max(5.0, min(wait_s, 900.0))),
            )
            if self.abort_requested:
                return

    async def _depart(self, params: TrainInput, config: dict[str, Any]) -> dict[str, Any]:
        """Un départ : promotion, soak, approbation, canary, vérification, rollback éventuel."""
        express = self.express
        self.express = False
        self.depart_requested = False
        if self.frozen:
            self.status = str(ReleaseStatus.FROZEN)
            await _wait(lambda: not self.frozen or self.abort_requested)
            return {"frozen": self.frozen}

        items = list(self.batch)
        self.batch = []
        self.status = str(ReleaseStatus.DEPARTING)
        release = await workflow.execute_activity(
            train_activities.create_release,
            {
                "project_slug": params.project_slug,
                "env": params.env,
                "batch_no": self.batch_no,
                "items": items,
                "express": express,
            },
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )
        self.current_release = release["release_id"]
        self.batch_no += 1

        promoted = await workflow.execute_activity(
            train_activities.promote,
            {"release_id": self.current_release, "project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=DEFAULT_RETRY,
        )
        if not promoted.get("ok"):
            return await self._rollback(params, config, "promotion impossible")

        self.status = str(ReleaseStatus.STAGING)
        soak_minutes = int(
            (config.get("express_soak_minutes") if express else config.get("soak_minutes")) or 10
        )
        smoke = await workflow.execute_activity(
            train_activities.run_smoke,
            {"release_id": self.current_release, "project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=NO_RETRY,
        )
        if not smoke.get("ok"):
            return await self._rollback(params, config, "smoke tests en échec")

        soak = await workflow.execute_activity(
            train_activities.soak,
            {
                "release_id": self.current_release,
                "project_slug": params.project_slug,
                "env": params.env,
                "minutes": soak_minutes,
            },
            start_to_close_timeout=timedelta(minutes=soak_minutes + 10),
            retry_policy=NO_RETRY,
        )
        if not soak.get("ok"):
            return await self._rollback(params, config, "SLO dégradés pendant le soak")

        approval = config.get("approval") or {}
        if approval.get("required"):
            self.status = str(ReleaseStatus.AWAITING_APPROVAL)
            self.approved = None
            await workflow.execute_activity(
                train_activities.request_approval,
                {
                    "release_id": self.current_release,
                    "project_slug": params.project_slug,
                    "env": params.env,
                    "group": approval.get("group"),
                },
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DEFAULT_RETRY,
            )
            timeout = timedelta(hours=int(approval.get("timeout_hours", 4)))
            got = await _wait(lambda: self.approved is not None or self.abort_requested, timeout)
            if self.abort_requested or self.approved is False or not got:
                self.batch = items + self.batch  # le lot repart au prochain tour
                self.status = str(ReleaseStatus.COLLECTING)
                await workflow.execute_activity(
                    train_activities.mark_release,
                    {
                        "release_id": self.current_release,
                        "status": "collecting",
                        "reason": "approbation refusée",
                    },
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=DEFAULT_RETRY,
                )
                return {"frozen": False, "reason": "approbation refusée"}

        # L'infra passe avant le code : appliquer Terraform après le canary reviendrait à
        # envoyer du code en production sur une infra qui ne l'attend pas encore.
        terraform = await workflow.execute_activity(
            train_activities.apply_terraform,
            {
                "release_id": self.current_release,
                "project_slug": params.project_slug,
                "env": params.env,
            },
            start_to_close_timeout=timedelta(minutes=45),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=NO_RETRY,
        )
        if terraform.get("ok") is False:
            return await self._rollback(params, config, terraform.get("reason", "apply Terraform en échec"))

        self.status = str(ReleaseStatus.PROMOTING)
        canary = config.get("canary") or {}
        steps = list(canary.get("steps", [100]))
        step_minutes = list(canary.get("step_minutes", [0] * len(steps)))
        for index, weight in enumerate(steps):
            analysis = await workflow.execute_activity(
                train_activities.promote_canary_step,
                {
                    "release_id": self.current_release,
                    "project_slug": params.project_slug,
                    "env": params.env,
                    "weight": weight,
                    "minutes": step_minutes[index] if index < len(step_minutes) else 0,
                },
                start_to_close_timeout=timedelta(
                    minutes=(step_minutes[index] if index < len(step_minutes) else 0) + 15
                ),
                retry_policy=NO_RETRY,
            )
            if not analysis.get("ok"):
                return await self._rollback(params, config, analysis.get("reason", "analyse canary KO"))

        self.status = str(ReleaseStatus.VERIFYING)
        verdict = await workflow.execute_activity(
            train_activities.verify_prod,
            {"release_id": self.current_release, "project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=NO_RETRY,
        )
        if not verdict.get("ok"):
            return await self._rollback(
                params, config, verdict.get("reason", "vérification post-déploiement KO")
            )

        self.status = str(ReleaseStatus.DONE)
        await workflow.execute_activity(
            train_activities.finish_release,
            {
                "release_id": self.current_release,
                "project_slug": params.project_slug,
                "env": params.env,
                "approved_by": self.approved_by,
            },
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
        return {"frozen": False, "release_id": self.current_release}

    async def _rollback(self, params: TrainInput, config: dict[str, Any], reason: str) -> dict[str, Any]:
        """Rollback : annuler, marquer, notifier, geler si la politique le demande."""
        self.status = str(ReleaseStatus.ROLLED_BACK)
        await workflow.execute_activity(
            train_activities.rollback,
            {
                "release_id": self.current_release,
                "project_slug": params.project_slug,
                "env": params.env,
                "reason": reason,
            },
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=DEFAULT_RETRY,
        )
        if config.get("freeze_on_rollback", True):
            self.frozen = True
            self.freeze_reason = reason
            self.status = str(ReleaseStatus.FROZEN)
        return {"frozen": self.frozen, "reason": reason}


async def _wait(condition: Any, timeout: timedelta | None = None) -> bool:  # noqa: ASYNC109
    """`wait_condition` qui rend `False` sur expiration plutôt que de lever."""
    if timeout is None:
        await workflow.wait_condition(condition)
        return True
    try:
        await workflow.wait_condition(condition, timeout=timeout)
    except TimeoutError:
        return False
    return True

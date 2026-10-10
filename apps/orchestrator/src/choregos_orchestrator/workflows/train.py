# SPDX-License-Identifier: Apache-2.0
"""`ReleaseTrain` : un singleton par projet × environnement (docs/plan/05 §5.2).

Le train est le deuxième des trois verrous : la merge queue garde `main` vert, le train
garde l'environnement, les garde-fous déclaratifs (fenêtres Argo, Environments GitHub)
tiennent même si Choregos tombe.
"""

from __future__ import annotations

import contextlib
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .planification import demarrer_activite, executer_activite

with workflow.unsafe.imports_passed_through():
    from choregos_contracts import InboundEventType, ReleaseStatus

    from ..activities import train as train_activities

DEFAULT_RETRY = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=2))
NO_RETRY = RetryPolicy(maximum_attempts=1)
#: Un environnement `auto_sync` prévient ses tickets (#278, S21-27). Avant ce marqueur, le train y
#: attendait `abort` sans rien lire, et un ticket qui y montait attendait 72 h puis un humain.
AUTO_SYNC_PREVIENT = "auto-sync-previent"
#: Le ticket dont le signal DÉMARRE le train (signal-with-start) reste dans le lot (S22-22). Avant ce
#: marqueur, `run` écrasait le lot avec les tickets reportés : sur le locataire dev, le 09/10, #4 et #6
#: ont réveillé chacun leur train et en ont été effacés aussitôt — le train attendait, vide, et le ticket
#: attendait une livraison qui ne viendrait pas.
LE_SIGNAL_DU_DEMARRAGE_EMBARQUE = "start-signal-boards"
#: Un départ express prend l'approbation de SA voie (`express_lane.approval`, #279, S22-17). Avant ce
#: marqueur, il prenait l'approbation ordinaire : la voie express n'était jamais lue.
EXPRESS_LANE_APPROVAL = "express-lane-approval"
#: Combien de fois, et à quel rythme, on regarde l'environnement avant de conclure.
ESSAIS_AUTO_SYNC = 30
PAUSE_AUTO_SYNC = timedelta(minutes=2)
#: Le train lit ce qu'Argo CD lui annonce (#280, S22-18). Avant ce marqueur, `deploy_event` rangeait
#: les événements dans `events` sans que rien ne les lise : seule la scrutation des étapes du départ
#: voyait une dégradation, à son prochain passage.
ECOUTE_LE_CD = "train-listens-to-cd"
#: Ce qui, annoncé pour l'environnement du départ, le fait revenir en arrière sans attendre la scrutation.
ALERTES_CD = frozenset({str(InboundEventType.CD_DEGRADED), str(InboundEventType.ROLLOUT_ABORTED)})


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
        self.ecoute_cd: bool = False

    # ───────────────────────── signaux ─────────────────────────

    @workflow.signal
    def merged(self, payload: dict[str, Any]) -> None:
        """Un ticket fusionné monte dans le train. Un doublon ne compte qu'une fois — mais l'approbation
        qu'il apporte s'ajoute : le webhook embarque le ticket sans elle, l'interpréteur avec."""
        for item in self.batch:
            if item.get("work_item_key") == payload.get("work_item_key"):
                item["approval"] = _cumul(item.get("approval"), payload.get("approval"))
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
        """Un événement d'Argo CD pour cet environnement (webhook `/webhooks/argocd`). Le départ le lit
        pendant le soak, le canary et la vérification (`_sous_l_oeil_du_cd`)."""
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
        # Le signal qui démarre le train est livré AVANT ce corps : le lot en porte déjà le ticket.
        deja_embarques, self.batch = list(self.batch), list(params.carried_items)
        if workflow.patched(LE_SIGNAL_DU_DEMARRAGE_EMBARQUE):
            for item in deja_embarques:
                self.merged(item)
        self.batch_no = params.batch_no
        self.frozen = params.frozen

        config = await executer_activite(
            train_activities.load_train_config,
            {"project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        if config.get("mode") == "auto_sync":
            # Pas de train : Argo suit `main`. Le workflow reste vivant pour les requêtes.
            self.status = "auto_sync"
            if not workflow.patched(AUTO_SYNC_PREVIENT):
                await _wait(lambda: self.abort_requested)
                return {"status": "auto_sync"}
            await self._suivre_la_synchro(params, payload)
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

    async def _suivre_la_synchro(self, params: TrainInput, payload: dict[str, Any]) -> None:
        """`auto_sync` : chaque ticket qui monte attend que l'environnement soit sain, puis est prévenu."""
        while not self.abort_requested:
            await _wait(lambda: bool(self.batch) or self.abort_requested)
            if self.abort_requested:
                return
            items, self.batch = list(self.batch), []
            synchro = f"auto-sync-{params.env}-{self.batch_no}"
            self.batch_no += 1
            for essai in range(ESSAIS_AUTO_SYNC):
                verdict = await executer_activite(
                    train_activities.confirmer_auto_sync,
                    {
                        "project_slug": params.project_slug,
                        "env": params.env,
                        "items": [str(item.get("work_item_key") or "") for item in items],
                        "sync_id": synchro,
                        "conclure": essai == ESSAIS_AUTO_SYNC - 1,
                    },
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=DEFAULT_RETRY,
                )
                if verdict.get("ok") is not None or self.abort_requested:
                    break
                await _wait(lambda: self.abort_requested, PAUSE_AUTO_SYNC)
            if workflow.info().get_current_history_length() > 15_000:
                workflow.continue_as_new(
                    {**payload, "carried_items": self.batch, "batch_no": self.batch_no, "frozen": self.frozen}
                )

    async def _collect(self, config: dict[str, Any]) -> None:
        """Attend le cron, le lot plein, un départ manuel ou un hotfix."""
        self.status = str(ReleaseStatus.COLLECTING)
        batch_max = int(config.get("batch_max", 8))
        while True:
            window = await executer_activite(
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
        self.ecoute_cd = workflow.patched(ECOUTE_LE_CD)
        if self.ecoute_cd:
            # Ce qu'Argo CD a annoncé pendant la collecte parle de la release d'avant : ce départ ne
            # revient pas en arrière pour elle (le smoke test regarde l'état réel juste après).
            self.events.clear()
        release = await executer_activite(
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

        promoted = await executer_activite(
            train_activities.promote,
            {"release_id": self.current_release, "project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=DEFAULT_RETRY,
        )
        if not promoted.get("ok"):
            return await self._rollback(params, config, "promotion failed")

        self.status = str(ReleaseStatus.STAGING)
        soak_minutes = int(
            (config.get("express_soak_minutes") if express else config.get("soak_minutes")) or 10
        )
        smoke = await executer_activite(
            train_activities.run_smoke,
            {"release_id": self.current_release, "project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=NO_RETRY,
        )
        if not smoke.get("ok"):
            return await self._rollback(params, config, "smoke tests failed")

        soak = await self._sous_l_oeil_du_cd(
            params,
            config,
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
            # La scrutation du soak garde sa raison d'avant ; une alerte d'Argo CD dit la sienne.
            raison = soak["reason"] if soak.get("argocd") else "SLOs degraded during the soak"
            return await self._rollback(params, config, raison)

        # L'approbation de la politique, OU celle qu'un ticket du lot apporte de son workflow (ADR
        # 0041) : un correctif part seul, une fonctionnalité attend son capitaine. Un embarquement
        # d'avant ne porte pas `approval` ; le calcul rend alors la politique seule, comme avant.
        politique = config.get("approval") or {}
        voie = (config.get("express_lane") or {}).get("approval")
        # Un départ express prend l'approbation de sa voie À LA PLACE de l'ordinaire — l'exigence
        # des tickets du lot tient toujours. Une voie qui n'en dit rien garde l'ordinaire.
        if express and voie is not None and workflow.patched(EXPRESS_LANE_APPROVAL):
            politique = voie
        approval = _approbation_du_depart(politique, items)
        if approval.get("required"):
            self.status = str(ReleaseStatus.AWAITING_APPROVAL)
            self.approved = None
            await executer_activite(
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
                await executer_activite(
                    train_activities.mark_release,
                    {
                        "release_id": self.current_release,
                        "status": "collecting",
                        "reason": "approval refused",
                    },
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=DEFAULT_RETRY,
                )
                return {"frozen": False, "reason": "approval refused"}

        # L'infra passe avant le code : appliquer Terraform après le canary reviendrait à
        # envoyer du code en production sur une infra qui ne l'attend pas encore.
        terraform = await executer_activite(
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
            return await self._rollback(params, config, terraform.get("reason", "Terraform apply failed"))

        self.status = str(ReleaseStatus.PROMOTING)
        canary = config.get("canary") or {}
        steps = list(canary.get("steps", [100]))
        step_minutes = list(canary.get("step_minutes", [0] * len(steps)))
        for index, weight in enumerate(steps):
            analysis = await self._sous_l_oeil_du_cd(
                params,
                config,
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
                return await self._rollback(params, config, analysis.get("reason", "canary analysis failed"))

        self.status = str(ReleaseStatus.VERIFYING)
        verdict = await self._sous_l_oeil_du_cd(
            params,
            config,
            train_activities.verify_prod,
            {"release_id": self.current_release, "project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=NO_RETRY,
        )
        if not verdict.get("ok"):
            return await self._rollback(params, config, verdict.get("reason", "post-deployment check failed"))

        self.status = str(ReleaseStatus.DONE)
        await executer_activite(
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

    async def _sous_l_oeil_du_cd(
        self, params: TrainInput, config: dict[str, Any], activite: Any, arg: Any, **options: Any
    ) -> Any:
        """Une étape du départ (soak, palier de canary, vérification) qu'Argo CD peut interrompre.

        La scrutation de l'étape reste le filet. Un `cd.app.degraded` ou un `cd.rollout.aborted` reçu
        pour l'environnement et les applications du départ la devance : l'étape est annulée et rend un
        verdict négatif, que l'appelant traite comme le sien (rollback). Les événements lus sont
        consommés : aucun n'est relu par l'étape suivante ni par le départ suivant.
        """
        if not self.ecoute_cd:
            return await executer_activite(activite, arg, **options)
        apps = [str(app) for app in config.get("apps") or []]
        etape = demarrer_activite(activite, arg, **options)
        await workflow.wait_condition(
            lambda: etape.done() or any(_alerte(e, params.env, apps) for e in self.events)
        )
        raison = self._prendre_l_alerte(params.env, apps)
        if raison is None:
            return await etape
        if not etape.done():
            etape.cancel()
        # L'étape annulée (ou échouée en même temps) ne dit plus rien : l'alerte d'Argo CD décide.
        with contextlib.suppress(ActivityError):
            await etape
        return {"ok": False, "reason": raison, "argocd": True}

    def _prendre_l_alerte(self, env: str, apps: list[str]) -> str | None:
        """Consomme les événements jusqu'à la première alerte qui concerne ce départ, et en rend la raison.

        Ceux d'un autre environnement ou d'une autre application sont écartés en passant ; ce qui suit
        l'alerte parle du même incident, que le rollback traite : tout est consommé."""
        while self.events:
            raison = _alerte(self.events.popleft(), env, apps)
            if raison is not None:
                self.events.clear()
                return raison
        return None

    async def _rollback(self, params: TrainInput, config: dict[str, Any], reason: str) -> dict[str, Any]:
        """Rollback : annuler, marquer, notifier, geler si la politique le demande."""
        self.status = str(ReleaseStatus.ROLLED_BACK)
        await executer_activite(
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


def _alerte(evenement: dict[str, Any], env: str, apps: list[str]) -> str | None:
    """La raison du rollback si l'événement est une alerte d'Argo CD pour cet environnement et l'une de
    ses applications ; `None` sinon. Un événement qui ne nomme ni environnement ni application compte,
    comme pour le ticket (ADR 0041) : un événement porté à la main garde son effet."""
    if str(evenement.get("type") or "") not in ALERTES_CD:
        return None
    detail = evenement.get("payload") or {}
    if detail.get("env") and str(detail["env"]) != env:
        return None
    app = str(detail.get("app") or "")
    if app and apps and app not in apps:
        return None
    sujet = app or env
    if str(evenement.get("type")) == str(InboundEventType.ROLLOUT_ABORTED):
        return f"Argo CD reported the {sujet} rollout aborted"
    return f"Argo CD reported {sujet} degraded"


def _cumul(une: dict[str, Any] | None, autre: dict[str, Any] | None) -> dict[str, Any] | None:
    """Deux exigences d'approbation d'un même ticket : exigée si l'une l'exige, le premier groupe dit."""
    if not une or not autre:
        return une or autre
    return {
        "required": bool(une.get("required") or autre.get("required")),
        "group": une.get("group") or autre.get("group"),
    }


def _approbation_du_depart(politique: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Exigée si la politique OU un ticket du lot l'exige. Le groupe : celui de la politique s'il est
    dit, sinon celui du premier ticket qui en nomme un ; le délai reste celui de la politique."""
    exigences = [i["approval"] for i in items if (i.get("approval") or {}).get("required")]
    if not exigences:
        return politique
    groupe = politique.get("group") or next((e.get("group") for e in exigences if e.get("group")), None)
    return {**politique, "required": True, "group": groupe}


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

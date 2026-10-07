# SPDX-License-Identifier: Apache-2.0
"""`ActionWorkflow` (ADR 0035) : une action approuvée, exécutée effet par effet — jamais dans la
requête qui l'approuve.

Chaque effet est une activité, sous sa clé (`<action>:<n>`) ; une panne passagère est retentée,
un refus définitif (`EffetRefuse`) arrête la suite. Ce qui avait été fait est alors COMPENSÉ, à
rebours, et l'action dit ce qu'elle n'a pas pu défaire. `action-<id>` : un seul workflow par action.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .planification import executer_activite

with workflow.unsafe.imports_passed_through():
    from ..activities import actions as activites

#: Un effet peut demander qu'on attende la PREUVE qu'il a servi (S20-08) : il rend
#: `{"attendre_une_preuve": {"jusqu_a": <iso>}}`, et le workflow attend le signal `preuve` jusque-là.
#: Les historiques d'avant ce marqueur n'avaient aucun effet qui le demandât.
PREUVE_ATTENDUE = "preuve-attendue"


class PreuveManquante(Exception):  # noqa: N818 - un refus nommé : la preuve n'est pas venue, ou dit non
    pass


LECTURE = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=2))
#: Un effet passager (429, 503, réseau) est retenté ; un refus ne l'est jamais.
EFFET = RetryPolicy(
    maximum_attempts=6,
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    non_retryable_error_types=["EffetRefuse", "ActionIntrouvable"],
)


@workflow.defn(name="ActionWorkflow", sandboxed=False)
class ActionWorkflow:
    def __init__(self) -> None:
        self.faits: list[int] = []
        self.etat = "running"
        #: Les preuves reçues, par position de l'effet qui les attendait.
        self.preuves: dict[int, dict[str, Any]] = {}

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"state": self.etat, "done": list(self.faits)}

    @workflow.signal
    def preuve(self, payload: dict[str, Any]) -> None:
        """Ce que l'extérieur a constaté (un rapport du collecteur, une livraison) : `ok` et `detail`."""
        self.preuves[int(payload.get("position", -1))] = dict(payload)

    @workflow.run
    async def run(self, entree: dict[str, Any]) -> dict[str, Any]:
        action_id = str(entree["action_id"])
        plan = await executer_activite(
            activites.charger_l_action,
            action_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=LECTURE,
        )
        try:
            for position in range(int(plan["effets"])):
                fait = await executer_activite(
                    activites.executer_l_effet,
                    {"action_id": action_id, "position": position},
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=EFFET,
                )
                self.faits.append(position)
                resultat = fait.get("result") if isinstance(fait, dict) else None
                attente = resultat.get("attendre_une_preuve") if isinstance(resultat, dict) else None
                if attente and workflow.patched(PREUVE_ATTENDUE):
                    await self._attendre_la_preuve(action_id, position, dict(attente))
        except (ActivityError, PreuveManquante) as erreur:
            if isinstance(erreur, ActivityError):
                cause = str(erreur.cause) if erreur.cause is not None else str(erreur)
            else:
                cause = str(erreur)
            self.etat = "compensating"
            compensations = []
            for position in reversed(self.faits):
                compensations.append(
                    await executer_activite(
                        activites.compenser_l_effet,
                        {"action_id": action_id, "position": position},
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=LECTURE,
                    )
                )
            self.etat = "failed"
            await executer_activite(
                activites.cloturer_l_action,
                {"action_id": action_id, "status": "failed", "error": cause, "compensations": compensations},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=LECTURE,
            )
            return {"status": "failed", "error": cause, "compensations": compensations}
        self.etat = "succeeded"
        await executer_activite(
            activites.cloturer_l_action,
            {"action_id": action_id, "status": "succeeded"},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=LECTURE,
        )
        return {"status": "succeeded", "effects": list(self.faits)}

    async def _attendre_la_preuve(self, action_id: str, position: int, attente: dict[str, Any]) -> None:
        """L'effet est fait ; l'action attend la preuve qu'il a servi, jusqu'à son échéance. Venue et
        favorable, la suite reprend ; contraire ou absente, l'action échoue et ce qui était fait se
        compense, comme pour un refus."""
        jusqu_a = datetime.fromisoformat(str(attente["jusqu_a"]))
        self.etat = "awaiting_evidence"
        await executer_activite(
            activites.marquer_l_attente,
            {
                "action_id": action_id,
                "position": position,
                "en_attente": True,
                "jusqu_a": jusqu_a.isoformat(),
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=LECTURE,
        )
        delai = max(jusqu_a - workflow.now(), timedelta(0))
        with contextlib.suppress(TimeoutError):
            await workflow.wait_condition(lambda: position in self.preuves, timeout=delai)
        preuve = self.preuves.get(position)
        if preuve is None:
            raise PreuveManquante(f"no evidence before {jusqu_a.isoformat()}: timed out")
        if not preuve.get("ok"):
            raise PreuveManquante(f"the evidence says no: {preuve.get('detail') or 'no detail'}")
        self.etat = "running"
        await executer_activite(
            activites.marquer_l_attente,
            {"action_id": action_id, "position": position, "en_attente": False, "preuve": preuve},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=LECTURE,
        )

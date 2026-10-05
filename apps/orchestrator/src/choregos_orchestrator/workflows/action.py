# SPDX-License-Identifier: Apache-2.0
"""`ActionWorkflow` (ADR 0035) : une action approuvée, exécutée effet par effet — jamais dans la
requête qui l'approuve.

Chaque effet est une activité, sous sa clé (`<action>:<n>`) ; une panne passagère est retentée,
un refus définitif (`EffetRefuse`) arrête la suite. Ce qui avait été fait est alors COMPENSÉ, à
rebours, et l'action dit ce qu'elle n'a pas pu défaire. `action-<id>` : un seul workflow par action.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .planification import executer_activite

with workflow.unsafe.imports_passed_through():
    from ..activities import actions as activites

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

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"state": self.etat, "done": list(self.faits)}

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
                await executer_activite(
                    activites.executer_l_effet,
                    {"action_id": action_id, "position": position},
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=EFFET,
                )
                self.faits.append(position)
        except ActivityError as erreur:
            cause = str(erreur.cause) if erreur.cause is not None else str(erreur)
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

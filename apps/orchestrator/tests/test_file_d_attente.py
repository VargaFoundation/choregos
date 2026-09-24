"""Un run qui attend une place ne doit pas se dire « en cours ».

Le plafond de simultanéité crée le Job SUSPENDU : il n'a aucun pod. Le marquer
« running » ferait mentir le board, la page du run et les mesures — rien ne tourne.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


class _ExecuteurAvecFile:
    """Un exécuteur qui met en file, puis admet. Comme `k8s_job`, sans cluster."""

    kind = ExecutorKind.K8S_JOB
    capabilities = frozenset({"queue"})

    def __init__(self) -> None:
        self.admis = False

    async def start(self, spec: StageJobSpec) -> ExecRef:
        return ExecRef(
            kind=self.kind, name=f"run-{spec.run_id}", namespace=spec.namespace, run_id=spec.run_id
        )

    async def status(self, ref: ExecRef) -> ExecStatus:
        if self.admis:
            return ExecStatus(state="running")
        return ExecStatus(state="pending", message="en attente d'une place (plafond 2 par namespace)")

    async def cancel(self, ref: ExecRef) -> None:
        return None

    def logs(self, ref: ExecRef) -> Any:
        raise NotImplementedError


async def _run_prepare(setup: Fixture) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activites

    return await activites.prepare_stage(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-implement",
            "role": "implement",
            "actor": "dev",
            "from_state": "ready",
            "to_state": "in_progress",
            "attempt": 1,
        }
    )


async def test_un_run_en_file_n_est_pas_dit_en_cours(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import stage as activites

    executeur = _ExecuteurAvecFile()
    setup.adapters.executor = executeur
    plan = await _run_prepare(setup)
    run_id = str(plan["run_id"])

    demarrage = await activites.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "run_id": run_id,
            "stage_input": plan["stage_input"],
        }
    )
    assert demarrage["queued"] is True

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        assert run.status == "queued", "aucun pod ne tourne : le dire"

    # La place se libère : le prochain relevé d'état doit le voir.
    executeur.admis = True
    await activites.await_run(
        {"project_id": setup.project_id, "run_id": run_id, "timeout_minutes": 0, "poll_seconds": 0.01}
    )
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None and run.status == "running", "admis, donc en cours"


async def test_preparer_deux_fois_la_meme_etape_ne_casse_pas(setup: Fixture) -> None:
    """Une activité Temporal peut repasser sur le MÊME run, et la clé de passerelle est
    déterministe. Un `INSERT` sec répondait « duplicate key value violates unique
    constraint » — une erreur de base remontée jusqu'au workflow, qui mourait. Vu sur le
    banc du 2026-09-24. AGENTS.md l'exige : rejouable sans effet double."""
    from choregos_api.db.models import GatewayKeyRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    premier = await _run_prepare(setup)
    second = await _run_prepare(setup)
    assert premier["run_id"] == second["run_id"], "l'identifiant de run est déterministe"

    async with session_scope() as session:
        lignes = (await session.execute(select(GatewayKeyRow))).scalars().all()
    assert len(lignes) == 1, "une seule clé, pas deux — ni une erreur de contrainte"

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

    # La place se libère : le prochain relevé d'état doit le voir. Le budget nul fait
    # ensuite expirer l'attente — et depuis le 2026-09-25 un run expiré est nettoyé ; ce
    # qui prouve l'admission est la date de démarrage et l'événement `run.started`.
    executeur.admis = True
    await activites.await_run(
        {"project_id": setup.project_id, "run_id": run_id, "timeout_minutes": 0, "poll_seconds": 0.01}
    )
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None and run.started_at is not None, "admis, donc démarré"
        from choregos_api.db.models import Event
        from sqlalchemy import select

        types = (
            (await session.execute(select(Event.type).where(Event.work_item_id == setup.work_item_id)))
            .scalars()
            .all()
        )
        assert any(t.endswith("run.started") for t in types), types


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


class _ExecuteurScripte(_ExecuteurAvecFile):
    """Une suite d'états par relevé, puis le dernier pour toujours ; `cancel` se note."""

    def __init__(self, *etats: ExecStatus) -> None:
        super().__init__()
        self.etats = list(etats)
        self.annules: list[str] = []

    async def status(self, ref: ExecRef) -> ExecStatus:
        if len(self.etats) > 1:
            return self.etats.pop(0)
        return self.etats[0]

    async def cancel(self, ref: ExecRef) -> None:
        self.annules.append(ref.name)


EN_FILE = ExecStatus(state="pending", message="en attente d'une place (plafond 2 par namespace)")
EN_COURS = ExecStatus(state="running")
FINI = ExecStatus(state="succeeded")


async def _demarre(setup: Fixture, executeur: Any) -> str:
    from choregos_orchestrator.activities import stage as activites

    setup.adapters.executor = executeur
    plan = await _run_prepare(setup)
    run_id = str(plan["run_id"])
    await activites.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "run_id": run_id,
            "stage_input": plan["stage_input"],
        }
    )
    return run_id


async def test_l_attente_en_file_ne_compte_pas_contre_le_budget_de_l_etape(setup: Fixture) -> None:
    """Deux runs RH ont « dépassé 20 min » sans qu'un pod ait tourné (banc du 2026-09-25) :
    leur Job attendait une place. Ici la file dure dix fois le budget de l'étape, et le
    run finit quand même bien."""
    from choregos_orchestrator.activities import stage as activites

    executeur = _ExecuteurScripte(*([EN_FILE] * 20), EN_COURS, FINI)
    run_id = await _demarre(setup, executeur)
    resultat = await activites.await_run(
        {
            "project_id": setup.project_id,
            "run_id": run_id,
            "timeout_minutes": 0.002,  # 0,12 s d'étape…
            "queue_timeout_minutes": 1,
            "poll_seconds": 0.01,  # …après 0,2 s de file
        }
    )
    assert resultat["status"] != "timed_out", resultat
    assert executeur.annules == [], "rien à nettoyer : le run a tourné"


async def test_un_run_jamais_admis_echoue_sur_la_file_et_retire_son_job(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import stage as activites

    executeur = _ExecuteurScripte(EN_FILE)
    run_id = await _demarre(setup, executeur)
    resultat = await activites.await_run(
        {
            "project_id": setup.project_id,
            "run_id": run_id,
            "timeout_minutes": 10,
            "queue_timeout_minutes": 0.002,
            "poll_seconds": 0.01,
        }
    )
    assert resultat["status"] == "queue_timed_out"
    assert resultat["result"]["reason"] == "queue_timeout" and "jamais admis" in resultat["result"]["summary"]
    assert executeur.annules == [f"run-{run_id}"], (
        "le Job suspendu est retiré : il ne bloquera pas les suivants"
    )
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None and run.status == "failed" and run.ended_at is not None


async def test_un_run_qui_depasse_son_budget_retire_aussi_son_job(setup: Fixture) -> None:
    from choregos_orchestrator.activities import stage as activites

    executeur = _ExecuteurScripte(EN_COURS)
    run_id = await _demarre(setup, executeur)
    resultat = await activites.await_run(
        {"project_id": setup.project_id, "run_id": run_id, "timeout_minutes": 0.002, "poll_seconds": 0.01}
    )
    assert resultat["status"] == "timed_out" and resultat["result"]["reason"] == "timeout"
    assert executeur.annules == [f"run-{run_id}"]

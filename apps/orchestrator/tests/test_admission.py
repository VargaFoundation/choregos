# SPDX-License-Identifier: Apache-2.0
"""Un greffon INSTALLÉ peut-il refuser qu'un run démarre — avant toute clé, et jamais après coup ?

Le greffon est un vrai module avec son `.dist-info` : c'est `charger_les_greffons()` qui le trouve
et qui appelle son `brancher()`, comme au démarrage d'un worker.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import func, select

from .conftest import Fixture

GREFFON = """
from choregos_core.admission import AdmissionRefusee, declarer_une_admission

#: Ce que le test règle : l'organisation « suspendue », et ce que le contrôle a vu.
ETAT = {"suspendues": set(), "vues": [], "panne": False}


async def organisation_active(demande):
    ETAT["vues"].append(demande)
    if ETAT["panne"]:
        raise ConnectionError("base des organisations injoignable")
    if demande.org in ETAT["suspendues"]:
        raise AdmissionRefusee(f"l'organisation {demande.org} est suspendue — la réactiver, puis relancer")


def brancher():
    declarer_une_admission("suspension", organisation_active)
"""


@pytest.fixture
def greffon(tmp_path: pathlib.Path) -> Iterator[Any]:
    from choregos_adapters import charger_les_greffons
    from choregos_core.admission import reinitialiser

    racine = tmp_path / "site"
    racine.mkdir()
    (racine / "greffon_admission.py").write_text(GREFFON, encoding="utf-8")
    info = racine / "greffon_admission-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: greffon_admission\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\ngreffon_admission = greffon_admission:brancher\n", encoding="utf-8"
    )
    sys.path.insert(0, str(racine))
    try:
        assert "greffon_admission" in charger_les_greffons()
        yield sys.modules["greffon_admission"].ETAT
    finally:
        sys.path.remove(str(racine))
        sys.modules.pop("greffon_admission", None)
        reinitialiser()


async def _preparer(setup: Fixture, attempt: int = 1) -> dict[str, Any]:
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
            "attempt": attempt,
        }
    )


async def _compter(modele: Any) -> int:
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return int((await session.execute(select(func.count()).select_from(modele))).scalar_one())


async def test_le_controle_recoit_ce_qu_il_faut_pour_decider(setup: Fixture, greffon: Any) -> None:
    prepared = await _preparer(setup)
    assert prepared["run_id"]
    (demande,) = greffon["vues"]
    assert (demande.org, demande.project, demande.work_item) == (
        "varga",
        setup.project_slug,
        setup.tracker_key,
    )
    assert demande.run_id == prepared["run_id"] and demande.role == "implement"
    assert demande.budget_usd > 0


async def test_un_refus_arrete_l_etape_sans_reprise_et_sans_cle(setup: Fixture, greffon: Any) -> None:
    from choregos_api.db.models import GatewayKeyRow, Run
    from temporalio.exceptions import ApplicationError

    greffon["suspendues"].add("varga")
    with pytest.raises(ApplicationError) as refus:
        await _preparer(setup)
    assert refus.value.type == "admission_refused" and refus.value.non_retryable
    # La raison nomme le contrôle ET dit quoi faire : c'est ce que le ticket affiche.
    assert "suspension : l'organisation varga est suspendue — la réactiver" in str(refus.value)
    assert await _compter(GatewayKeyRow) == 0, "une clé a été émise pour un run refusé"
    assert await _compter(Run) == 0, "un run a été créé alors qu'il était refusé"


async def test_un_run_deja_prepare_n_est_jamais_refuse_apres_coup(setup: Fixture, greffon: Any) -> None:
    """Rejouer `prepare_stage` (reprise Temporal) après une suspension ne tue pas ce qui tourne."""
    premier = await _preparer(setup)
    greffon["suspendues"].add("varga")
    rejoue = await _preparer(setup)
    assert rejoue["reused"] and rejoue["run_id"] == premier["run_id"]


async def test_une_panne_du_controle_n_est_pas_un_refus(setup: Fixture, greffon: Any) -> None:
    """Elle remonte telle quelle, et Temporal retente : une base injoignable ne décide rien."""
    from temporalio.exceptions import ApplicationError

    greffon["panne"] = True
    with pytest.raises(ConnectionError):
        await _preparer(setup)
    try:
        await _preparer(setup)
    except ApplicationError:  # pragma: no cover - ce serait le défaut
        pytest.fail("une panne du contrôle a été transformée en refus définitif")
    except ConnectionError:
        pass


async def test_sans_greffon_tout_run_est_admis(setup: Fixture) -> None:
    from choregos_core.admission import admissions_declarees

    assert admissions_declarees() == ()
    assert (await _preparer(setup))["run_id"]

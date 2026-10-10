"""`adr_number_free` lit la branche par défaut À TRAVERS LE DÉPÔT, au moment de juger (#287, S22-20).

Deux études qui tournent ensemble numérotent leur ADR « le plus grand existant plus un » : la
seconde à fusionner porterait un doublon. L'activité liste les ADR déjà sur `main` et la garantie
refuse le numéro pris.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

DEPOT = "varga/billing-api"
REGLE = {"name": "adr_number_free"}


async def test_un_numero_pris_sur_la_branche_par_defaut_est_refuse(setup: Any) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    charge = {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "gates": [REGLE]}
    scm = setup.adapters.scm
    scm.set_diff(DEPOT, "main", "choregos/123", [("docs/adr/0042-rls.md", 16, 0)])
    [libre] = await evaluate_gates(charge)
    assert libre["passed"] is True, libre["detail"]

    # Une autre étude a fusionné son 0042 entre-temps.
    await scm.commit_files(DEPOT, "main", {"docs/adr/0042-cache.md": "# Cache\n"}, "autre étude")
    [pris] = await evaluate_gates(charge)
    assert pris["passed"] is False
    assert pris["annotations"] == ["docs/adr/0042-rls.md"]
    assert "already taken on the default branch by docs/adr/0042-cache.md" in pris["detail"]


async def test_sans_listage_du_depot_la_garantie_refuse(setup: Any, monkeypatch: Any) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    async def en_panne(*_: Any) -> list[str]:
        raise RuntimeError("GitHub injoignable")

    monkeypatch.setattr(setup.adapters.scm, "list_files", en_panne)
    setup.adapters.scm.set_diff(DEPOT, "main", "choregos/123", [("docs/adr/0042-rls.md", 16, 0)])
    charge = {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "gates": [REGLE]}
    [aveugle] = await evaluate_gates(charge)
    assert aveugle["passed"] is False and "default branch unavailable" in aveugle["detail"]

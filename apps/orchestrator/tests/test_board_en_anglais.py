"""Les champs du board GitHub Projects se lisent en anglais (S22-21, ADR 0039, finding #290).

L'orchestrateur écrit `Cost (€)`, `Size`, `Risk` ; le provisioning renomme les champs français d'un
board créé avant (`Coût (€)`, `Taille`, `Risque`) au lieu d'en créer de seconds ; les gabarits livrés
nomment les champs anglais.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest
import yaml

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

RACINE = pathlib.Path(__file__).resolve().parents[3]


async def test_le_commentaire_de_suivi_ecrit_les_champs_du_board_en_anglais(setup: Fixture) -> None:
    from choregos_orchestrator.activities.tracker import update_status_comment

    await update_status_comment({"project_id": setup.project_id, "work_item_id": setup.work_item_id})

    ecrits = [args for nom, args in setup.adapters.tracker.calls if nom == "set_fields"]
    assert len(ecrits) == 1
    cle, champs = ecrits[0]
    assert cle == setup.tracker_key
    assert set(champs) == {"Cost (€)", "Size", "Risk", "Run"}
    assert champs["Size"] == "M" and champs["Risk"] == "low"


async def test_le_pas_du_board_renomme_un_board_d_avant_sans_doubler_ses_champs() -> None:
    from choregos_adapters.fakes.tracker import FakeTracker
    from choregos_orchestrator.activities.provisioning import _github_ensure_project_board

    tracker = FakeTracker()
    tracker.board_fields = ["Status", "Coût (€)", "Taille", "Risque"]
    bundle = SimpleNamespace(adapters=SimpleNamespace(tracker=tracker))

    message = await _github_ensure_project_board({}, bundle, None)

    assert tracker.board_fields == ["Status", "Cost (€)", "Size", "Risk", "Run"]
    assert message == (
        "board fields: 1 kept, 1 created, 3 renamed (Coût (€) → Cost (€); Taille → Size; Risque → Risk)"
    )
    # Rejouable (ADR 0008) : un second passage ne trouve plus rien à faire.
    assert (
        await _github_ensure_project_board({}, bundle, None) == "board fields: 5 kept, 0 created, 0 renamed"
    )
    assert tracker.board_fields == ["Status", "Cost (€)", "Size", "Risk", "Run"]


@pytest.mark.parametrize("gabarit", ["github-tekton-argo-k8s", "github-aca"])
async def test_les_gabarits_livres_nomment_les_champs_anglais(gabarit: str) -> None:
    manifeste = yaml.safe_load((RACINE / "templates" / gabarit / "manifest.yaml").read_text(encoding="utf-8"))
    pas = [
        p["github.ensure_project_board"]
        for p in manifeste["steps"]
        if isinstance(p, dict) and "github.ensure_project_board" in p
    ]
    assert pas == [{"fields": ["Status", "Cost (€)", "Size", "Risk", "Run"]}]


async def test_sans_numero_de_board_le_pas_le_dit_au_lieu_de_compter_zero() -> None:
    from choregos_orchestrator.activities.provisioning import _github_ensure_project_board

    async def ensure_project_fields(fields: list[str]) -> dict[str, list[str]]:
        return {"kept": [], "renamed": [], "created": []}

    bundle = SimpleNamespace(
        adapters=SimpleNamespace(tracker=SimpleNamespace(ensure_project_fields=ensure_project_fields))
    )
    message = await _github_ensure_project_board({}, bundle, None)
    assert message == "no Projects v2 board configured: give its number to the connector"
